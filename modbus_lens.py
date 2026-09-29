#!/usr/bin/env python3
"""modbus-lens: reconnaissance tool for unknown Modbus RTU devices.

Point it at a serial port (or run the built-in demo) and it will:
  1. discover which slave addresses answer (1-247)
  2. map readable register ranges (block reads, illegal-address aware)
  3. probe discovered registers: sample N times, classify each as
     static / changing, and flag value ranges that look like
     percent / tenths-of-unit encodings

Design principle inherited from SerialLens: report only evidence, never
claim to know what a register "means".

Demo mode simulates two virtual slaves (a temperature/humidity sensor and
an I/O module) in memory, so the full workflow runs with no hardware.
Serial mode needs pyserial.

Single file, stdlib only (pyserial optional). Python >= 3.9.
"""

import argparse
import math
import struct
import sys
import time

__version__ = "0.1.0"

FUNC_READ_HOLDING = 0x03
FUNC_READ_INPUT = 0x04
EXC_ILLEGAL_ADDRESS = 0x02
EXC_ILLEGAL_FUNCTION = 0x01

CLASS_EXCEPTION = "exception"
CLASS_TIMEOUT = "timeout"
CLASS_OK = "ok"


class TransportError(Exception):
    pass


class Timeout(TransportError):
    pass


class ExceptionResponse(TransportError):
    def __init__(self, func, code):
        super().__init__("slave returned exception func=0x%02X code=0x%02X" % (func, code))
        self.code = code


# ---------------------------------------------------------------- CRC

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def frame(addr, func, payload):
    body = bytes([addr, func]) + payload
    crc = crc16(body)
    return body + bytes([crc & 0xFF, crc >> 8])


def check_crc(data: bytes):
    if len(data) < 4:
        raise TransportError("frame too short (%d bytes)" % len(data))
    want = crc16(data[:-2])
    got = data[-2] | (data[-1] << 8)
    if want != got:
        raise TransportError("CRC mismatch (got 0x%04X want 0x%04X)" % (got, want))


# ---------------------------------------------------------------- transports

class DemoTransport:
    """Two virtual slaves so the workflow runs with zero hardware.

    slave 1: temperature/humidity sensor
      reg 0: temperature x10, sinusoidal around 250 (25.0 C)
      reg 1: humidity x10, slow drift around 600 (60.0 %)
      reg 2: firmware version (static)
      reg 3: serial number high (static)
      reg 4: status bits (static)
      regs 20-25: "calibration" block (static)
    slave 17: I/O module
      regs 0-7: channel states (static per run)
    """

    SLAVES = (1, 17)

    def __init__(self):
        t = time.time()
        self.io_channels = [0x0100 + i * 0x11 for i in range(8)]
        self._t0 = t

    def _slave1(self, addr):
        t = time.time() - self._t0
        temp = 250 + int(30 * math.sin(t / 30.0) + 5 * math.sin(t / 5.0))
        hum = 600 + int(80 * math.sin(t / 120.0 + 1.0) + 15 * math.sin(t / 8.0))
        table = {0: temp & 0xFFFF, 1: hum & 0xFFFF, 2: 0x0107, 3: 0xA3F1,
                 4: 0x0005}
        table.update({20 + i: (0xC0DE + i * 0x101) & 0xFFFF for i in range(6)})
        return table.get(addr)

    def _slave17(self, addr):
        if 0 <= addr < 8:
            return self.io_channels[addr]
        return None

    def request(self, addr, func, payload):
        if addr not in self.SLAVES:
            raise Timeout("no answer from slave %d" % addr)
        if func not in (FUNC_READ_HOLDING, FUNC_READ_INPUT):
            raise ExceptionResponse(func | 0x80, EXC_ILLEGAL_FUNCTION)
        start, qty = struct.unpack(">HH", payload)
        if not 1 <= qty <= 125:
            raise ExceptionResponse(func | 0x80, 0x03)
        table_fn = self._slave1 if addr == 1 else self._slave17
        values = [table_fn(a) for a in range(start, start + qty)]
        if any(v is None for v in values):
            raise ExceptionResponse(func | 0x80, EXC_ILLEGAL_ADDRESS)
        data = b"".join(struct.pack(">H", v) for v in values)
        return frame(addr, func, bytes([len(data)]) + data)


class SerialTransport:
    """Modbus RTU over a real serial port (requires pyserial)."""

    def __init__(self, port, baud=9600, timeout=0.4):
        try:
            import serial  # pyserial
        except ImportError:
            raise SystemExit("error: serial mode needs pyserial: pip install pyserial")
        try:
            self.ser = serial.Serial(port, baud, timeout=timeout)
        except serial.SerialException as e:
            raise SystemExit("error: cannot open %s: %s" % (port, e))
        self.timeout = timeout

    def request(self, addr, func, payload):
        # read-only by design: reject any non-read function code before it
        # can reach the wire. modbus-lens is a recon tool - it never writes.
        if func not in (FUNC_READ_HOLDING, FUNC_READ_INPUT):
            raise ValueError(
                "modbus-lens is read-only: func 0x%02X rejected "
                "(allowed: 0x03 read holding, 0x04 read input)" % func)
        self.ser.reset_input_buffer()
        self.ser.write(frame(addr, func, payload))
        # read header: addr + func (+ bytecount for reads)
        head = self._read_exact(2)
        if head[0] != addr:
            # late answer from a previous (slower) slave: it would be
            # silently mis-attributed to this address - treat as no answer
            self.ser.reset_input_buffer()
            raise Timeout("answer from slave %d, expected %d" % (head[0], addr))
        if head[1] & 0x80:
            rest = self._read_exact(3)  # exception code + crc
            check_crc(head + rest)
            raise ExceptionResponse(head[1] & 0x7F, rest[0])
        bc = self._read_exact(1)[0]
        rest = self._read_exact(bc + 2)
        raw = head + bytes([bc]) + rest
        check_crc(raw)
        return raw

    def _read_exact(self, n):
        buf = b""
        deadline = time.time() + self.timeout
        while len(buf) < n and time.time() < deadline:
            chunk = self.ser.read(n - len(buf))
            if chunk:
                buf += chunk
        if len(buf) < n:
            raise Timeout("serial read timeout")
        return buf


# ---------------------------------------------------------------- scanning

def scan_slaves(tr, lo=1, hi=247, func=FUNC_READ_HOLDING):
    found = []
    verbose = isinstance(tr, SerialTransport)
    if verbose:
        sys.stderr.write("scanning slaves %d-%d (worst case ~%d s at 0.4 s timeout)...\n"
                         % (lo, hi, int((hi - lo + 1) * 0.4)))
    for addr in range(lo, hi + 1):
        if verbose and (addr - lo) % 16 == 0:
            sys.stderr.write("\rscanning... addr %d    " % addr)
        try:
            tr.request(addr, func, struct.pack(">HH", 0, 1))
            found.append(addr)
        except ExceptionResponse:
            found.append(addr)  # an exception still proves the slave is alive
        except (Timeout, TransportError):
            pass  # no answer or unusable frame: treat as absent
    if verbose:
        sys.stderr.write("\n")
    return found


def scan_registers(tr, slave, lo=0, hi=999, block=8, func=FUNC_READ_HOLDING):
    """Return readable ranges [(start, end)] using block reads.

    A block that answers proves the whole span it covers (per spec the
    slave would raise illegal-address otherwise); blocks raising
    illegal-address are probed one-by-one because real devices
    sometimes have holes inside a block. Any timeout breaks the
    current range (continuity is then unproven).
    """
    ranges = []
    range_start = None
    last_ok = None
    block = max(1, min(125, block))  # RTU allows at most 125 regs per read

    def close():
        nonlocal range_start, last_ok
        if range_start is not None:
            ranges.append((range_start, last_ok))
        range_start = None
        last_ok = None

    addr = lo
    while addr <= hi:
        qty = min(block, hi - addr + 1)
        try:
            tr.request(slave, func, struct.pack(">HH", addr, qty))
            if range_start is None:
                range_start = addr
            last_ok = addr + qty - 1
            addr += qty
            continue
        except ExceptionResponse as e:
            if e.code != EXC_ILLEGAL_ADDRESS:
                close()
                addr += qty
                continue
            for a in range(addr, addr + qty):  # hole-probe within block
                try:
                    tr.request(slave, func, struct.pack(">HH", a, 1))
                    if range_start is None:
                        range_start = a
                    last_ok = a
                except ExceptionResponse:
                    close()
                except (Timeout, TransportError):
                    close()
            addr += qty
        except (Timeout, TransportError):
            close()
            addr += qty
    close()
    return ranges


def probe_registers(tr, slave, addrs, samples=8, interval=0.15,
                    func=FUNC_READ_HOLDING):
    """Sample each register; return {addr: stats-dict} for alive ones."""
    stats = {a: [] for a in addrs}
    for _ in range(samples):
        for a in addrs:
            try:
                raw = tr.request(slave, func, struct.pack(">HH", a, 1))
                val = struct.unpack(">H", raw[3:5])[0]
                stats[a].append(val)
            except (TransportError, struct.error):
                pass
        time.sleep(interval)
    out = {}
    for a, vals in stats.items():
        if not vals:
            continue
        lo, hi = min(vals), max(vals)
        mean = sum(vals) / len(vals)
        spread = hi - lo
        entry = {"min": lo, "max": hi, "mean": mean, "samples": len(vals),
                 "changing": spread > 0, "hunch": value_hunch(lo, hi, mean)}
        out[a] = entry
    return out


def value_hunch(lo, hi, mean):
    """Evidence-based guesses only; never a claim."""
    hunches = []
    if lo == hi:
        pass
    elif hi <= 100:
        hunches.append("range fits 0-100 (percent?)")
    elif hi <= 1000 and mean > 150:
        hunches.append("looks like x10 fixed-point (e.g. 250 -> 25.0)")
    if hi > 40000:
        hunches.append("uses high bits (bitfield or unsigned large)")
    return hunches


# ---------------------------------------------------------------- report

def classify_probe(stats):
    changing = [a for a, s in stats.items() if s["changing"]]
    static = [a for a, s in stats.items() if not s["changing"]]
    return changing, static


def run_report(tr, args):
    lines = []
    lines.append("modbus-lens %s" % __version__)
    lines.append("target: %s" % ("demo (virtual slaves %s)" % (", ".join(map(str, DemoTransport.SLAVES)))
                                 if isinstance(tr, DemoTransport) else "serial %s @ %d" % (args.port, args.baud)))
    lines.append("")
    slaves = scan_slaves(tr, args.slave_lo, args.slave_hi)
    lines.append("== Slave discovery (addr %d-%d): %d found" % (
        args.slave_lo, args.slave_hi, len(slaves)))
    for s in slaves:
        lines.append("  - slave %d responds" % s)
    lines.append("")

    for slave in slaves:
        regs = scan_registers(tr, slave, args.reg_lo, args.reg_hi)
        lines.append("== Slave %d readable ranges (regs %d-%d):" % (slave, args.reg_lo, args.reg_hi))
        for lo, hi in regs:
            lines.append("  - %d .. %d (%d regs)" % (lo, hi, hi - lo + 1))
        lines.append("")
        addrs = [a for lo, hi in regs for a in range(lo, hi + 1)]
        if args.probe and addrs:
            stats = probe_registers(tr, slave, addrs, samples=args.probe)
            changing, static = classify_probe(stats)
            lines.append("== Slave %d probe (%d samples/reg):" % (slave, args.probe))
            for a in changing:
                s = stats[a]
                lines.append("  - reg %-5d CHANGING  min=%-6d max=%-6d mean=%.1f  %s" % (
                    a, s["min"], s["max"], s["mean"],
                    ("; ".join(s["hunch"])) if s["hunch"] else ""))
            for a in static:
                lines.append("  - reg %-5d static    value=%d (0x%04X)" % (
                    a, stats[a]["min"], stats[a]["min"]))
            lines.append("")
    lines.append("Done. Values are evidence, not meanings - confirm with the device docs.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="modbus-lens",
        description="Reconnaissance for unknown Modbus RTU devices: slave discovery, "
                    "register mapping and evidence-based probing. All output to stdout.")
    ap.add_argument("--port", help="serial port, e.g. COM3 or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--demo", action="store_true",
                    help="run against built-in virtual slaves (no hardware)")
    ap.add_argument("--slave-lo", type=int, default=1, help="slave scan lower bound (default 1)")
    ap.add_argument("--slave-hi", type=int, default=247, help="slave scan upper bound (default 247)")
    ap.add_argument("--reg-lo", type=int, default=0, help="register scan lower bound (default 0)")
    ap.add_argument("--reg-hi", type=int, default=200, help="register scan upper bound (default 200)")
    ap.add_argument("--probe", type=int, default=None, metavar="N",
                    help="sample each register N times and classify (demo default: 8, 0 disables)")
    ap.add_argument("--version", action="version", version="modbus-lens " + __version__)
    args = ap.parse_args(argv)

    if args.demo and args.port:
        ap.error("--demo and --port are mutually exclusive")
    if not (1 <= args.slave_lo <= args.slave_hi <= 247):
        ap.error("slave range must be within 1..247")
    if not (0 <= args.reg_lo <= args.reg_hi <= 65535):
        ap.error("register range must be within 0..65535")
    if not args.demo and not args.port:
        ap.error("give --port or --demo (try the demo first: modbus-lens --demo)")

    tr = DemoTransport() if args.demo else SerialTransport(args.port, args.baud)
    if args.demo and args.probe is None:
        args.probe = 8
    print(run_report(tr, args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
