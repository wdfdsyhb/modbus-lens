# modbus-lens

**Reconnaissance for unknown Modbus RTU devices.** Point it at a serial port
(or run the built-in demo) and it discovers which slave addresses answer,
maps readable register ranges, then probes them and reports what the
*evidence* looks like — without ever claiming to know what a register means.

Sibling project of [SerialLens](https://github.com/wdfdsyhb/SerialLens)
(same philosophy: face unknown hardware, report only evidence) and
[comfy-preflight](https://github.com/wdfdsyhb/comfy-preflight).

```bash
python modbus_lens.py --demo                 # full workflow on virtual slaves, no hardware
python modbus_lens.py --port COM3 --baud 9600 --probe 8
```

Demo output (trimmed):

```
== Slave discovery (addr 1-247): 2 found
  - slave 1 responds

== Slave 1 readable ranges (regs 0-200):
  - 0 .. 4 (5 regs)
  - 20 .. 25 (6 regs)

== Slave 1 probe (8 samples/reg):
  - reg 0     CHANGING  min=250 max=252 mean=250.6  looks like x10 fixed-point (e.g. 250 -> 25.0)
  - reg 2     static    value=263 (0x0107)
```

## What it does

| Step | How |
|---|---|
| Slave discovery | Read Holding Registers (0x03), 1 register, addr 1-247; an exception response still proves the slave is alive |
| Register mapping | Block reads (default 8); an answering block proves its whole span; illegal-address blocks are hole-probed one register at a time |
| Probing | N samples per register: min/max/mean, CHANGING vs static, hex of static values |
| Hunches | Evidence-based only: "range fits 0-100 (percent?)", "looks like x10 fixed-point", "uses high bits". Never a claim. |

## Demo mode

`--demo` simulates two virtual slaves in memory:

- **slave 1** — temperature/humidity sensor: temp (x10, moving), humidity
  (x10, moving), firmware version, serial, status bits, calibration block
- **slave 17** — I/O module: 8 static channel registers

so you can see discovery → mapping → probing in seconds without a single
wire attached.

## Real hardware

```bash
pip install pyserial
python modbus_lens.py --port COM3 --baud 9600 [--reg-hi 999] [--probe 8]
```

- The full slave sweep (1-247) takes ~100 s at the default 0.4 s timeout;
  progress is printed to stderr so silence doesn't look like a hang.
- Register scan speed is bounded by the bus (65535 registers at 9600 baud
  is hours), so keep `--reg-hi` sane first; widen after the first map.
- Probing is a burst (N samples within a short window): minute-scale drift
  will read as static. Corrupted frames (CRC mismatch) are skipped, never
  fatal - a noisy link bends the map, it doesn't kill the scan.

## Hints

- `--probe 0` skips probing (discovery + mapping only)
- `--slave-lo/--slave-hi`, `--reg-lo/--reg-hi` bound the scans
- Probing reads are single-register; interleaved writes from other masters
  will show up as noise — that is evidence too

## License

MIT
