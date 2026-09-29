# modbus-lens 中文说明

**面向未知 Modbus RTU 设备的侦察工具**：自动发现从站地址、扫描可读寄存器
区间、逐寄存器采样探测，报告"证据长什么样"——但绝不声称知道寄存器"是什么"。
设计原则继承自 [SerialLens](https://github.com/wdfdsyhb/SerialLens)。

```bash
python modbus_lens.py --demo          # 内置两个虚拟从站，无硬件跑全流程
python modbus_lens.py --port COM3 --baud 9600 --probe 8
```

三步流程：

1. **从站发现**：读保持寄存器 0x03 逐地址试探（1-247），返回异常响应也算"从站活着"
2. **寄存器扫描**：块读（默认 8 个），能读通的块整段有效；报非法地址的块逐个探测找洞
3. **探测分类**：每寄存器采样 N 次 → CHANGING / static + 量程启发式（"像 0-100 的百分比""像 x10 定点数"），只报证据不下结论

`--demo` 内置：1 号从站=温湿度传感器（温度湿度在动、固件版本/校准块静态），
17 号从站=IO 模块。单文件、纯标准库（串口模式需 `pip install pyserial`），
Python ≥ 3.9。MIT 许可。

**默认只读**：所有总线操作都是读——功能码在代码层白名单锁定为 0x03/0x04
（其余一律拒绝），不存在写寄存器的代码路径。工业总线上，这是底线。

## Lens 系列

modbus-lens 是 **Lens 系列**的一员——同一套理念：面对未知硬件与协议，只报告证据，不下断言。

| 工具 | 领域 | 状态 |
| --- | --- | --- |
| [SerialLens](https://github.com/wdfdsyhb/SerialLens) | 串口 / UART 协议识别与解码 | ✅ 可用 |
| **modbus-lens** | Modbus RTU 从站发现与寄存器侦察 | ✅ 可用 |
| can-lens | CAN 总线流量画像 | 🚧 规划中 |
| ble-lens | BLE GATT 服务侦察 | 💡 构想中 |
