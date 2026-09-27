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
