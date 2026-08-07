我们用的舵机型号是HP/RP8-U45H-M
gimbal_sdk/Reference_Source code for STM32

# 工程结构

## 1. 概要

讲解了舵机演示例程的工程结构规范，以及库文件的作用。

## 2. 工程结构

以例程**舵机通讯检测**为例，讲解STM32的工程结构。

解压 [openmv-stm32.zip](../data/openmv-stm32.zip)，打开文件夹 *源代码/FashionStarUartServo*

目录结构如下:

* `Project`

Keil5的工程文件，点击 `FashionStarUartServo.uvprojx` 即可通过Keil5打开此工程。

* `User`

主程序以及用户自定义库

* `main.c` 用户主程序
* 用户自定义的库文件，例如*串口舵机驱动库*等
* `Libraries`
* `CMSIS`：存放ARM公司为cm3内核专一定制的内核文件接口
* `FWLIB`：存放STM32的外设库
* `Listings` ：该目录是MDK生成信息输出目录，存放代码分布（.map和.lst）
* `Output` 该目录是MDK生成信息输出目录，存放目标文件(.o)、调试文件(.axf)、下载文件(.hex)、依赖文件（.d）等。

## 3. 用户自定义库文件

User文件夹 工程结构介绍

* `sys_tick`

管理系统时间。通过配置系统定时器中断，实现了延时以及倒计时的逻辑。

* `ring_buffer`

用C语言实现了环形缓冲队列。用于存放管理串口通信的字节流。同时还具备向缓冲队列读取/写入特定数据类型的数值的功能。

* `usart`

串口通信的库。通过配置宏，可以方便的打开关闭STM32F103C8开发板上三个USART资源。

* `fashion_star_uart_servo`

封装了舵机的通信协议，是舵机STM32F103版本的SDK。

* `main.c`

主程序。程序入口。

`User` 文件目录树

```
├── fashion_star_uart_servo 舵机驱动库
│   ├── fashion_star_uart_servo.c
│   └── fashion_star_uart_servo.h
├── main.c 主程序
├── ring_buffer 环形缓冲队列驱动库
│   ├── README.md
│   ├── ring_buffer.c
│   └── ring_buffer.h
├── stm32f10x_conf.h
├── sys_tick　系统时钟
│   ├── sys_tick.c
│   └── sys_tick.h
└── usart 串口通信
    ├── README.md
    ├── usart.c
    └── usart.h
```

# 总线伺服舵机PID调节指南

您拿到手的总线伺服舵机，或是云台、机械手臂等产品，出厂前已完成对应参数设置。如需修改（例如云台应用），建议先参考下表给出的参数范围。

## 1. 简易修改

### 1.1 推荐参考值

| 型号 | Kp | Ki | Kd |
| --- | --- | --- | --- |
| HA/RA8-U25 | 1750 | 1 | 7005 |
| HA/RA8-U25-M | 800 | 0 | 5000 |
| HA/RA8-U25H-M | 400 | 0 | 2000 |
| HP/RP8-U45-M | 320 | 0 | 2500 |
| HP/RP8-U45H-M | 300 | 0 | 700 |
| HX/RX8-U28H-M | 800 | 0 | 80 |

### 1.2 初始默认值

| 型号 | Kp | Ki | Kd |
| --- | --- | --- | --- |
| HA/RA8-U25 | 800 | 1 | 5000 |
| HA/RA8-U25-M | 800 | 0 | 5000 |
| HA/RA8-U25H-M | 400 | 0 | 2300 |
| HP/RP8-U45-M | 320 | 0 | 1000 |
| HP/RP8-U45H-M | 300 | 0 | 700 |
| HX/RX8-U28H-M | 600 | 0 | 50 |

### 1.3 使用舵机上位机写入 PID 参数

1. 依次点击：①【串口开关】→ ②【目标 ID 舵机】→ ③【模式选择】→ ④【参数选项】；
2. 点击展开 ⑤【内部参数】，修改需要的参数值；
3. 点击【写入参数】保存设置，断电重启后生效。

Note

**务必保证** Kp 与 Hold Kp、Kd 与 Hold Kd 一致。

Tip

若无法写入，请先记录当前参数，在多圈模式中点击【停止】，重新上电后再尝试写入。

## 2. 进阶修改

### 2.1 PID 基础原理与调节思路

* **Kp（比例增益）**：提高响应速度，减小误差，但过大可能引发超调和震荡。
* **Ki（积分增益）**：消除稳态误差，但可能造成系统震荡。多数情况下可设为 0。
* **Kd（微分增益）**：提升系统稳定性，抑制超调，对噪声较敏感。

说明：本文以“控制精度”为调节目标。不同负载对应的 PID 参数会有所差异。如对精度要求不高、仅需“丝滑运行”，建议使用出厂默认参数。

### 2.2 舵机 PID 参数调节建议

#### 2.2.1 比例参数（Kp）调节步骤

* 将 Ki、Hold Ki、Kd、Hold Kd 设置为 0（仅保留比例项）。
* 逐步提高 Kp、Hold Kp，直到系统出现轻微振荡。
* 再逐步降低比例值，直至系统稳定不振荡。
* 记录此时比例值，最终设定为其 60%~70%。

#### 2.2.2 积分参数（Ki）调节建议

* 默认设为 0，仅当 PD 无法满足精度要求时，考虑设为 1。
* 若系统出现震荡或抖动，应立即取消积分项。

#### 2.2.3 微分参数（Kd）调节步骤

* 在比例项调节完毕并固定后，逐步增大 Kd、Hold Kd。
* 当系统出现振荡，逐步降低微分值，直到系统重新稳定。
* 最终设定为不振荡状态下数值的约 30%。

### 2.3 调节过程中注意事项

* **联调建议**：先空载测试，再带载联调，最后微调 PID。
* **组合使用**：P 为必用项，通常配合 D 或 I 使用，推荐组合如下：
* PD：追求系统稳定。
* PI：追求响应速度。
* PID：用于兼顾稳定与速度，但调节难度较大。
* **实际经验**：实际使用中，两项组合（如 PD）往往已能满足需求。

## 3. 结语

PID 调节是提升舵机系统性能的重要手段。通过合理设定比例、积分与微分参数，可在不同负载与应用场景下实现理想的控制效果。建议结合实际工况，逐项调试达到期望效果。

# 舵机通讯检测[¶](#_1 "Permanent link")

## 1. 舵机通讯检测指令[¶](#1 "Permanent link")

检查舵机是否在线，就需要用到PING指令。PING数据包的内容部分是舵机的ID。如果ID号的舵机存在且在线，总线伺服舵机在接收到PING指令时，会发送一个响应包。如果ID号的舵机不存在或者掉线，就不会有舵机发送响应数据包。

## 2. 操作流程[¶](#2 "Permanent link")

1 . **物理接线**
①STM32与STLinkV2相连接
②STM32的UART1接总线伺服舵机转接板
③总线伺服舵机转接板上接一个舵机ID为0的总线伺服舵机
④STM32的UART2接USB转TTL模块
> 注: 接线部分请参考*1.4接线说明*

2 . **打开STM32例程的工程文件**
3 . **代码编译**
日志输出：编译成功
4 . **将STLinkV2与电脑的USB相连接**
5 . **将固件下载到STM32**
固件载入到STM32 Flash成功的日志输出
6 . **给舵机供电**
电池接在UART舵机转接板上。
7 . **将USB转TTL与电脑USB口相连接**
8 . **打开串口调试助手软件**
选择对应的设备号，配置波特率为115200，打开串口。
> 注: 这里以友善串口调试助手为例

9 . **重置STM32**
按下STM32开发板上的**RESET** 重置按钮。STM32执行新写入的固件。
> 注：只有按下重置按键，STM32才会执行新烧录的固件。

10 . **查看串口调试助手上的日志信息**

## 3. 硬件资源初始化[¶](#3 "Permanent link")

首先是引入四个依赖的库文件。

```
#include "stm32f10x.h"
#include "usart.h"
#include "sys_tick.h"
#include "fashion_star_uart_servo.h"
```

第一个`stm32f10x.h`是STM32F103的标准库，剩下三个是自定义的库。

* `sys_tick.h`

管理系统时间，通过配置系统定时器中断，实现了延时以及倒计时的逻辑。

* `usart.h`

串口通信的库。通过配置宏，可以方便的打开关闭STM32F103C8开发板上三个USART资源。

* `fashion_star_uart_servo.h`

封装了Fashion Star 总线伺服舵机的通信协议，是总线伺服舵机STM32F103版本的SDK。

首先定义两个`Usart_DataTypeDef` 结构体指针。

串口1用于舵机控制，指针`servoUsart`指向`usart1`。

```
 Usart_DataTypeDef* servoUsart = &usart1;
```

串口2用于日志输出，指针`loggingUsart`指向`usart2`。

```
Usart_DataTypeDef* loggingUsart = &usart2;
```

接下来依次进行滴答定时器初始化与串口初始化。

```
// 嘀嗒定时器初始化
SysTick_Init();
// 串口初始化
Usart_Init();
```

## 4. API使用说明[¶](#4-api "Permanent link")

接下来就是舵机通讯检测函数`FSUS_Ping`，依次传入串口数据结构体指针`servoUsart`，还有舵机的ID号`servoId`。

```
statusCode = FSUS_Ping(servoUsart, servoId);
```

`statusCode`是返回的状态码`FSUS_STATUS`，如果是请求成功则返回`0`，如果是其他的数值则意味着舵机通讯检测失败。可以在`fashion_star_uart_servo.h` 文件里面查阅不同的statusCode对应的错误。

```
// FSUS状态码
#define FSUS_STATUS uint8_t
#define FSUS_STATUS_SUCCESS 0 // 设置/读取成功
#define FSUS_STATUS_FAIL 1 // 设置/读取失败
#define FSUS_STATUS_TIMEOUT 2 // 等待超时
#define FSUS_STATUS_WRONG_RESPONSE_HEADER 3 // 响应头不对
#define FSUS_STATUS_UNKOWN_CMD_ID 4 // 未知的控制指令
#define FSUS_STATUS_SIZE_TOO_BIG 5 // 参数的size大于FSUS_PACK_RESPONSE_MAX_SIZE里面的限制
#define FSUS_STATUS_CHECKSUM_ERROR 6 // 校验和错误
#define FSUS_STATUS_ID_NOT_MATCH 7 // 请求的舵机ID跟反馈回来的舵机ID不匹配
```

## 5. 主程序完整源码[¶](#5 "Permanent link")

```
/********************************************************
 * 测试舵机PING指令，测试舵机是否在线
 ********************************************************/
#include "stm32f10x.h"
#include "usart.h"
#include "sys_tick.h"
#include "fashion_star_uart_servo.h"

// 使用串口1作为舵机控制的端口
// <接线说明>
// STM32F103 PA9(Tx)  <----> 总线伺服舵机转接板 Rx
// STM32F103 PA10(Rx) <----> 总线伺服舵机转接板 Tx
// STM32F103 GND      <----> 总线伺服舵机转接板 GND
// STM32F103 V5       <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART1_ENABLE为1
// 设置完成之后, 将下行取消注释
Usart_DataTypeDef* servoUsart = &usart1;

// 使用串口2作为日志输出的端口
// <接线说明>
// STM32F103 PA2(Tx) <----> USB转TTL Rx
// STM32F103 PA3(Rx) <----> USB转TTL Tx
// STM32F103 GND     <----> USB转TTL GND
// STM32F103 V5      <----> USB转TTL 5V (可选)
// <注意事项>
// 使用前确保已设置usart.h里面的USART2_ENABLE为1
Usart_DataTypeDef* loggingUsart = &usart2;

// 重定向c库函数printf到串口，重定向后可使用printf函数
int fputc(int ch, FILE *f)
{
    while((loggingUsart->pUSARTx->SR&0X40)==0){}
    /* 发送一个字节数据到串口 */
    USART_SendData(loggingUsart->pUSARTx, (uint8_t) ch);
    /* 等待发送完毕 */
    // while (USART_GetFlagStatus(USART1, USART_FLAG_TC) != SET);
    return (ch);
}

// 连接在转接板上的总线伺服舵机ID号
uint8_t servoId = 0;
// 发送Ping请求的状态码
FSUS_STATUS statusCode;

int main (void)
{
    // 嘀嗒定时器初始化
    SysTick_Init();
    // 串口初始化
    Usart_Init();

    while (1)
    {
        printf("\r\n");
        // Ping一下舵机
        printf("[INFO]ping servo %d \r\n", servoId);
        statusCode = FSUS_Ping(servoUsart, servoId);
        printf("[INFO]status code %d \r\n", statusCode);

        // 根据状态码做不同的处理
        if (statusCode == FSUS_STATUS_SUCCESS){
            printf("[INFO]ping success, servo %d echo \r\n", servoId);
        }else{
            printf("[ERROR]ping fail, servo %d not online \r\n", servoId);
        }
        // 等待1000ms
        SysTick_DelayMs(1000);
    }
}
```

# 舵机角度控制[¶](#_1 "Permanent link")

## 1. API使用说明[¶](#1-api "Permanent link")

### 1.1. 舵机角度控制[¶](#11 "Permanent link")

```
FSUS_STATUS FSUS_SetServoAngle(Usart_DataTypeDef *usart, uint8_t servoId, float angle, uint16_t interval, uint16_t power, uint8_t wait);
```

* `usart` 舵机控制对应的串口数据对象`Usart_DataTypeDef`
* `servoId` 舵机的ID
* `angle` 舵机的目标角度，取值范围\([-135.0, 135.0]\)，精度精确到\(0.1°\)
* `interval` 舵机角度的旋转周期，单位ms
* `power` 舵机执行功率 mV 默认为0
* `wait` 设置舵机角度的时候，是否为阻塞式
* `0`：不阻塞
* `1`：等待舵机旋转到特定的位置

**使用示例**

```
//// 舵机控制相关的参数
// 舵机的ID号
uint8_t servoId = 0;
// 舵机的目标角度
// 舵机角度在-135度到135度之间, 精确到小数点后一位
float angle = 0;
// 时间间隔ms
// 可以尝试修改设置更小的时间间隔，例如500ms
uint16_t interval = 2000;
// 舵机执行功率 mV 默认为0
uint16_t power = 0;
// 设置舵机角度的时候, 是否为阻塞式
// 0:不等待 1:等待舵机旋转到特定的位置;
uint8_t wait = 1;

FSUS_SetServoAngle(servoUsart, servoId, angle, interval, power, wait);
```

### 1.2. 舵机角度查询[¶](#12 "Permanent link")

```
// 查询单个舵机的角度信息 angle 单位度
FSUS_STATUS FSUS_QueryServoAngle(Usart_DataTypeDef *usart, uint8_t servoId, float *angle);
```

* `usart` 舵机控制对应的串口数据对象`Usart_DataTypeDef`
* `servoId` 舵机的ID
* `curAngle` 舵机当前的角度，取值范围\([-135.0, 135.0]\)，精度精确到\(0.1°\)

**使用示例**

```
uint8_t servoId = 0;    // 舵机的ID号
float curAngle = 0;     // 舵机当前所在的角度
FSUS_QueryServoAngle(servoUsart, servoId, &curAngle); // 读取一下舵机的角度
```

## 2. 控制单个舵机[¶](#2 "Permanent link")

### 2.1. 功能简介[¶](#21 "Permanent link")

测试控制舵机的角度, 让舵机在两个角度之间做周期性旋转。

### 2.2. 效果视频[¶](#22 "Permanent link")

### [![img](https://wiki.fashionrobo.com/openmvstm32/images/servo-angle-control/%E6%8E%A7%E5%88%B6%E5%8D%95%E4%B8%AA%E8%88%B5%E6%9C%BA.PNG)](https://www.bilibili.com/video/av81425657)[¶](#_2 "Permanent link")

### 2.3. 源代码[¶](#23 "Permanent link")

```
#include "stm32f10x.h"
#include "usart.h"
#include "sys_tick.h"
#include "fashion_star_uart_servo.h"

// 使用串口1作为舵机控制的端口
// <接线说明>
// STM32F103 PA9(Tx)    <----> 总线伺服舵机转接板 Rx
// STM32F103 PA10(Rx) <----> 总线伺服舵机转接板 Tx
// STM32F103 GND        <----> 总线伺服舵机转接板 GND
// STM32F103 V5         <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART1_ENABLE为1
// 设置完成之后, 将下行取消注释
Usart_DataTypeDef* servoUsart = &usart1;

// 使用串口2作为舵机控制的端口
// <接线说明>
// STM32F103 PA2(Tx)    <----> 总线伺服舵机转接板 Rx
// STM32F103 PA3(Rx)    <----> 总线伺服舵机转接板 Tx
// STM32F103 GND        <----> 总线伺服舵机转接板 GND
// STM32F103 V5     <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART2_ENABLE为1
// 设置完成之后, 将下行取消注释
// Usart_DataTypeDef* servoUsart = &usart2;

// 使用串口3作为舵机控制的端口
// <接线说明>
// STM32F103 PB10(Tx) <----> 总线伺服舵机转接板 Rx
// STM32F103 PB11(Rx) <----> 总线伺服舵机转接板 Tx
// STM32F103 GND        <----> 总线伺服舵机转接板 GND
// STM32F103 V5         <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART1_ENABLE为1
// 设置完成之后, 将下行取消注释
// Usart_DataTypeDef* servoUsart = &usart3;

//// 舵机控制相关的参数
// 舵机的ID号
uint8_t servoId = 0;
// 舵机的目标角度
// 舵机角度在-135度到135度之间, 精确到小数点后一位
float angle = 0;
// 时间间隔ms
// 可以尝试修改设置更小的时间间隔，例如500ms
uint16_t interval = 2000;
// 舵机执行功率 mV 默认为0
uint16_t power = 0;
// 设置舵机角度的时候, 是否为阻塞式
// 0:不等待 1:等待舵机旋转到特定的位置;
uint8_t wait = 1;

int main (void)
{
    // 嘀嗒定时器初始化
    SysTick_Init();
    // 串口初始化
    Usart_Init();

    while (1)
    {
        // 控制舵机角度
        angle = 135.0;
        FSUS_SetServoAngle(servoUsart, servoId, angle, interval, power, wait);

        // 等待2s
        SysTick_DelayMs(2000);

        // 控制舵机旋转到另外一个角度
        angle = -135.0;
        FSUS_SetServoAngle(servoUsart, servoId, angle, interval, power, wait);

        // 等待2s
        SysTick_DelayMs(2000);
    }
}
```

## 3. 控制多个舵机[¶](#3 "Permanent link")

### 3.1. 功能简介[¶](#31 "Permanent link")

演示如何同时控制多几个舵机。

### 3.2. 效果视频[¶](#32 "Permanent link")

[![img](https://wiki.fashionrobo.com/openmvstm32/images/servo-angle-control/%E8%88%B5%E6%9C%BA%E8%A7%92%E5%BA%A6%E6%8E%A7%E5%88%B6-%E6%8E%A7%E5%88%B6%E5%A4%9A%E4%B8%AA%E8%88%B5%E6%9C%BA.png)](https://www.bilibili.com/video/av81425758)

### 3.3. 源代码[¶](#33 "Permanent link")

```
#include "stm32f10x.h"
#include "usart.h"
#include "sys_tick.h"
#include "fashion_star_uart_servo.h"

// 使用串口1作为舵机控制的端口
// <接线说明>
// STM32F103 PA9(Tx)    <----> 总线伺服舵机转接板 Rx
// STM32F103 PA10(Rx) <----> 总线伺服舵机转接板 Tx
// STM32F103 GND        <----> 总线伺服舵机转接板 GND
// STM32F103 V5         <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART1_ENABLE为1
// 设置完成之后, 将下行取消注释
Usart_DataTypeDef* servoUsart = &usart1;

//// 舵机控制相关的参数
// 时间间隔ms
// 可以尝试修改设置更小的时间间隔，例如500ms
uint16_t interval = 2000;
// 舵机执行功率 mV 默认为0
uint16_t power = 0;
// 设置舵机角度的时候, 是否为阻塞式
// 0:不等待 1:等待舵机旋转到特定的位置;
uint8_t wait = 0;

int main (void)
{
    // 嘀嗒定时器初始化
    SysTick_Init();
    // 串口初始化
    Usart_Init();

    while (1)
    {
        // 控制舵机云台角度
        FSUS_SetServoAngle(servoUsart, 0, 135.0, interval, power, wait);
        FSUS_SetServoAngle(servoUsart, 1, 45.0, interval, power, wait);
        // 等待动作完成
        SysTick_DelayMs(interval);

        // 等待2s
        SysTick_DelayMs(2000);

        // 控制舵机旋转到另外一个角度
        FSUS_SetServoAngle(servoUsart, 0, -135.0, interval, power, wait);
        FSUS_SetServoAngle(servoUsart, 1, -45.0, interval, power, wait);
        // 等待动作完成
        SysTick_DelayMs(interval);

        // 等待2s
        SysTick_DelayMs(2000);
    }
}
```

## 4. 平均转速与角度回读[¶](#4 "Permanent link")

### 4.1. 功能简介[¶](#41 "Permanent link")

控制舵机角度-平均转速与角度回读 1. 查询当前角度，根据平均转速和目标角度估计执行周期。

1. 通过周期性查询舵机角度，判断舵机是否到达目标角度。
2. 测试舵机的死区(Dead Block)/稳态误差。

### 4.2. 效果视频[¶](#42 "Permanent link")

[![img](https://wiki.fashionrobo.com/openmvstm32/images/servo-angle-control/%E8%88%B5%E6%9C%BA%E8%A7%92%E5%BA%A6%E6%8E%A7%E5%88%B6-%E5%B9%B3%E5%9D%87%E8%BD%AC%E9%80%9F%E4%B8%8E%E8%A7%92%E5%BA%A6%E5%9B%9E%E8%AF%BB.png)](https://www.bilibili.com/video/av81426180)

### 4.3. 源代码[¶](#43 "Permanent link")

```
#include "stm32f10x.h"
#include "usart.h"
#include "sys_tick.h"
#include "fashion_star_uart_servo.h"

// 使用串口1作为舵机控制的端口
// <接线说明>
// STM32F103 PA9(Tx)    <----> 总线伺服舵机转接板 Rx
// STM32F103 PA10(Rx)   <----> 总线伺服舵机转接板 Tx
// STM32F103 GND        <----> 总线伺服舵机转接板 GND
// STM32F103 V5         <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART1_ENABLE为1
// 设置完成之后, 将下行取消注释
Usart_DataTypeDef* servoUsart = &usart1;

// 使用串口2作为日志输出的端口
// <接线说明>
// STM32F103 PA2(Tx) <----> USB转TTL Rx
// STM32F103 PA3(Rx) <----> USB转TTL Tx
// STM32F103 GND     <----> USB转TTL GND
// STM32F103 V5      <----> USB转TTL 5V (可选)
// <注意事项>
// 使用前确保已设置usart.h里面的USART2_ENABLE为1
Usart_DataTypeDef* loggingUsart = &usart2;

// 重定向c库函数printf到串口，重定向后可使用printf函数
int fputc(int ch, FILE *f)
{
    while((loggingUsart->pUSARTx->SR&0X40)==0){}
    /* 发送一个字节数据到串口 */
    USART_SendData(loggingUsart->pUSARTx, (uint8_t) ch);
    /* 等待发送完毕 */
    // while (USART_GetFlagStatus(USART1, USART_FLAG_TC) != SET);
    return (ch);
}

// 舵机控制相关的参数
uint8_t servoId = 0;    // 舵机的ID
float curAngle = 0;     // 舵机当前所在的角度
float nextAngle = 0;    // 舵机的目标角度
uint16_t speed = 200;   // 舵机的转速 单位 °/s
uint16_t interval = 0;  // 舵机旋转的周期
uint16_t power = 0;     // 舵机执行功率 mV 默认为0   W
uint8_t wait = 0;       // 0:不等待 1:等待舵机旋转到特定的位置;
// 舵机角度死区, 如果舵机当前角度跟
// 目标角度相差小于死区则代表舵机到达目标角度, 舵机不再旋转
// <注意事项>
//      死区跟舵机的型号有关系, 取决于舵机固件的设置, 不同型号的舵机会有差别
float servoDeadBlock = 1.0;

// 查询舵机的角度
uint16_t calcIntervalMs(uint8_t servoId, float nextAngle, float speed){
    // 读取一下舵机的角度
    FSUS_QueryServoAngle(servoUsart, servoId, &curAngle);
    // 计算角度误差
    float dAngle =  (nextAngle > curAngle) ? (nextAngle - curAngle) : (curAngle - nextAngle);
    // 计算所需的时间
    return (uint16_t)((dAngle / speed) * 1000.0);
}

// 等待舵机进入空闲状态IDLE, 即舵机到达目标角度
void waitUntilServoIDLE(uint8_t servoId, float nextAngle){

    while(1){
        // 读取一下舵机的角度
        FSUS_QueryServoAngle(servoUsart, servoId, &curAngle);

        // 判断舵机是否达到目标角度
        float dAngle =  (nextAngle > curAngle) ? (nextAngle - curAngle) : (curAngle - nextAngle);

        // 打印一下当前的舵机角度
        printf("curAngle: %f dAngle: %f\r\n", curAngle, dAngle);

        // 判断是否小于死区
        if (dAngle <= servoDeadBlock){
            break;
        }
        // 等待一小段时间
        SysTick_DelayMs(5);
    }
}

int main (void)
{
    // 嘀嗒定时器初始化
    SysTick_Init();
    // 串口初始化
    Usart_Init();

    while (1)
    {
        // 设置舵机的目标角度
        nextAngle = 120.0;
        // 根据转速还有角度误差计算周期
        interval = calcIntervalMs(servoId, nextAngle, speed);
        printf("Set Servo %f-> %f", curAngle, nextAngle);
        // 控制舵机角度
        FSUS_SetServoAngle(servoUsart, servoId, nextAngle, interval, power, wait);
        // SysTick_DelayMs(interval);
        SysTick_DelayMs(5);
        waitUntilServoIDLE(servoId, nextAngle);

        // 等待1s 看舵机死区范围
        SysTick_DelayMs(1000);
        // 读取一下舵机的角度
        FSUS_QueryServoAngle(servoUsart, servoId, &curAngle);
        printf("Final Angle: %f", curAngle);
        SysTick_DelayMs(1000);

        // 设置舵机的目标角度
        nextAngle = -120;
        // 根据转速还有角度误差计算周期
        interval = calcIntervalMs(servoId, nextAngle, speed);
        // 控制舵机角度
        FSUS_SetServoAngle(servoUsart, servoId, nextAngle, interval, power, wait);
        // 需要延时一会儿，确保舵机接收并开始执行舵机控制指令
        // 如果马上发送舵机角度查询信息,新发送的这条指令可能会覆盖舵机角度控制信息
        SysTick_DelayMs(5);
        waitUntilServoIDLE(servoId, nextAngle);

        // 等待1s 看舵机死区范围
        SysTick_DelayMs(1000);
        // 读取一下舵机的角度
        FSUS_QueryServoAngle(servoUsart, servoId, &curAngle);
        printf("Final Angle: %f", curAngle);
        SysTick_DelayMs(1000);
    }
}
```

# 舵机阻尼模式[¶](#_1 "Permanent link")

## 1. API使用说明[¶](#1-api "Permanent link")

### 1.1. 设置阻尼模式并设置功率[¶](#11 "Permanent link")

```
// 舵机阻尼模式
FSUS_STATUS FSUS_DampingMode(Usart_DataTypeDef *usart, uint8_t servoId, uint16_t power);
```

* `usart` 舵机控制对应的串口数据对象`Usart_DataTypeDef`
* `servoId` 舵机的ID
* `power` 舵机的功率，单位mW

**使用示例**

```
// 连接在转接板上的总线伺服舵机ID号
uint8_t servoId = 0;
// 阻尼模式下的功率，功率越大阻力越大
uint16_t power = 500;
// 设置舵机为阻尼模式
FSUS_DampingMode(servoUsart, servoId, power);
```

## 2. 设置阻尼模式的功率[¶](#2 "Permanent link")

### 2.1. 功能简介[¶](#21 "Permanent link")

设置不同的功率，体验舵机阻尼力的变换。

### 2.2. 源代码[¶](#22 "Permanent link")

```
/***************************************************
 * 总线伺服舵机阻尼模式
 ***************************************************/
#include "stm32f10x.h"
#include "usart.h"
#include "sys_tick.h"
#include "fashion_star_uart_servo.h"

// 使用串口1作为舵机控制的端口
// <接线说明>
// STM32F103 PA9(Tx)  <----> 总线伺服舵机转接板 Rx
// STM32F103 PA10(Rx) <----> 总线伺服舵机转接板 Tx
// STM32F103 GND      <----> 总线伺服舵机转接板 GND
// STM32F103 V5       <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART1_ENABLE为1
// 设置完成之后, 将下行取消注释
Usart_DataTypeDef* servoUsart = &usart1;

// 连接在转接板上的总线伺服舵机ID号
uint8_t servoId = 0;
// 阻尼模式下的功率，功率越大阻力越大
uint16_t power = 500;
int main (void)
{
    // 嘀嗒定时器初始化
    SysTick_Init();
    // 串口初始化
    Usart_Init();
    // 设置舵机为阻尼模式
    FSUS_DampingMode(servoUsart, servoId, power);
    while (1)
    {
        //主循环什么也不做
        // 等待1000ms
        SysTick_DelayMs(1000);
    }
}
```

## 3. 阻尼模式与角度回读[¶](#3 "Permanent link")

### 3.1. 功能简介[¶](#31 "Permanent link")

设置舵机为阻尼模式，同时请求舵机的角度。在旋转舵机的情况下，每隔一段时间就更新一下舵机的角度。串口2每隔一段时间打印一下舵机角度信息。

### 3.2. 效果视频[¶](#32 "Permanent link")

[![img](https://wiki.fashionrobo.com/openmvstm32/images/servo-damping-mode/%E9%98%BB%E5%B0%BC%E6%A8%A1%E5%BC%8F-%E9%98%BB%E5%B0%BC%E6%A8%A1%E5%BC%8F%E4%B8%8E%E8%A7%92%E5%BA%A6%E5%9B%9E%E8%AF%BB.png)](https://www.bilibili.com/video/av81427383)

### 3.3 源代码[¶](#33 "Permanent link")

```
#include "stm32f10x.h"
#include "usart.h"
#include "sys_tick.h"
#include "fashion_star_uart_servo.h"

// 使用串口1作为舵机控制的端口
// <接线说明>
// STM32F103 PA9(Tx)  <----> 总线伺服舵机转接板 Rx
// STM32F103 PA10(Rx) <----> 总线伺服舵机转接板 Tx
// STM32F103 GND      <----> 总线伺服舵机转接板 GND
// STM32F103 V5       <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART1_ENABLE为1
// 设置完成之后, 将下行取消注释
Usart_DataTypeDef* servoUsart = &usart1;
// 使用串口2作为日志输出的端口
// <接线说明>
// STM32F103 PA2(Tx) <----> USB转TTL Rx
// STM32F103 PA3(Rx) <----> USB转TTL Tx
// STM32F103 GND     <----> USB转TTL GND
// STM32F103 V5      <----> USB转TTL 5V (可选)
// <注意事项>
// 使用前确保已设置usart.h里面的USART2_ENABLE为1
Usart_DataTypeDef* loggingUsart = &usart2;

// 重定向c库函数printf到串口，重定向后可使用printf函数
int fputc(int ch, FILE *f)
{
    while((loggingUsart->pUSARTx->SR&0X40)==0){}
    /* 发送一个字节数据到串口 */
    USART_SendData(loggingUsart->pUSARTx, (uint8_t) ch);
    /* 等待发送完毕 */
    // while (USART_GetFlagStatus(USART1, USART_FLAG_TC) != SET);
    return (ch);
}

FSUS_STATUS statusCode; // 请求包的状态码
uint8_t servoId = 0;    // 连接在转接板上的总线伺服舵机ID号
uint16_t power = 500;   // 阻尼模式下的功率，功率越大阻力越大
float angle = 0;        // 舵机的角度

int main (void)
{
    // 嘀嗒定时器初始化
    SysTick_Init();
    // 串口初始化
    Usart_Init();
    // 设置舵机为阻尼模式
    FSUS_DampingMode(servoUsart, servoId, power);
    while (1)
    {
        // 读取一下舵机的角度
        statusCode = FSUS_QueryServoAngle(servoUsart, servoId, &angle);

        if (statusCode ==FSUS_STATUS_SUCCESS){
            // 成功的读取到了舵机的角度
            printf("[INFO] servo id= %d ; angle = %f\r\n", servoId, angle);
        }else{
            // 没有正确的读取到舵机的角度
            printf("\r\n[INFO] read servo %d angle, status code: %d \r\n", servoId, statusCode);
            printf("[ERROR]failed to read servo angle\r\n");
        }
        // 等待1000ms
        SysTick_DelayMs(500);
    }
}
```


# 舵机用户自定义数据[¶](#_1 "Permanent link")

## 1. API使用说明[¶](#1-api "Permanent link")

### 1.1. 重置用户自定义数据[¶](#11 "Permanent link")

```
// 重置舵机的用户资料
FSUS_STATUS FSUS_ResetUserData(Usart_DataTypeDef *usart, uint8_t servoId);
```

* `usart` 舵机控制对应的串口数据对象`Usart_DataTypeDef`
* `servoId` 舵机的ID

**使用示例**

```
uint8_t servoId = 0;        // 连接在转接板上的总线伺服舵机ID号
FSUS_ResetUserData(servoUsart, servoId);
```

### 1.2. 读取用户自定义数[¶](#12 "Permanent link")

```
// 读取数据
FSUS_STATUS FSUS_ReadData(Usart_DataTypeDef *usart, uint8_t servoId,  uint8_t address, uint8_t *value, uint8_t *size);
```

* `usart` 舵机控制对应的串口数据对象`Usart_DataTypeDef`
* `servoId` 舵机的ID
* `address` 用户自定义数据的ID号
* `value` 读取到的数值
* `size` 读取到的数值的长度

**使用示例**

```
// 读取用户自定义数据
// 数据表里面的数据字节长度一般为1个字节/2个字节/4个字节
// 查阅通信协议可知,舵机角度上限的数据类型是有符号短整型(UShort, 对应STM32里面的int16_t),长度为2个字节
// 所以这里设置value的数据类型为int16_t
int16_t value;
uint8_t dataSize;
// 传参数的时候, 要将value的指针强行转换为uint8_t
FSUS_ReadData(servoUsart, servoId, FSUS_PARAM_ANGLE_LIMIT_HIGH, (uint8_t *)&value, &dataSize);
```

### 1.3. 写入用户自定义数据[¶](#13 "Permanent link")

```
// 写入数据
FSUS_STATUS FSUS_WriteData(Usart_DataTypeDef *usart, uint8_t servoId, uint8_t address, uint8_t *value, uint8_t size);
```

* `usart` 舵机控制对应的串口数据对象`Usart_DataTypeDef`
* `servoId` 舵机的ID
* `address` 地址位
* `value` 写入的数据
* `size` 写入的数据的长度

**使用示例**

```
uint8_t servoId = 0;        // 连接在转接板上的总线伺服舵机ID号
float angleLimitLow = -90.0;    // 舵机角度下限 (默认值-135)
value = (int16_t)(angleLimitLow*10); // 舵机角度下限 转换单位为0.1度
statusCode = FSUS_WriteData(servoUsart, servoId, FSUS_PARAM_ANGLE_LIMIT_LOW, (uint8_t *)&value, 2);
```

## 2. 重置用户自定义数据[¶](#2 "Permanent link")

用户自定义数据重置

```
#include "stm32f10x.h"
#include "usart.h"
#include "sys_tick.h"
#include "fashion_star_uart_servo.h"

// 使用串口1作为舵机控制的端口
// <接线说明>
// STM32F103 PA9(Tx)  <----> 总线伺服舵机转接板 Rx
// STM32F103 PA10(Rx) <----> 总线伺服舵机转接板 Tx
// STM32F103 GND      <----> 总线伺服舵机转接板 GND
// STM32F103 V5       <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART1_ENABLE为1
// 设置完成之后, 将下行取消注释
Usart_DataTypeDef* servoUsart = &usart1;

// 使用串口2作为日志输出的端口
// <接线说明>
// STM32F103 PA2(Tx) <----> USB转TTL Rx
// STM32F103 PA3(Rx) <----> USB转TTL Tx
// STM32F103 GND     <----> USB转TTL GND
// STM32F103 V5      <----> USB转TTL 5V (可选)
// <注意事项>
// 使用前确保已设置usart.h里面的USART2_ENABLE为1
Usart_DataTypeDef* loggingUsart = &usart2;

// 重定向c库函数printf到串口，重定向后可使用printf函数
int fputc(int ch, FILE *f)
{
    while((loggingUsart->pUSARTx->SR&0X40)==0){}
    /* 发送一个字节数据到串口 */
    USART_SendData(loggingUsart->pUSARTx, (uint8_t) ch);
    /* 等待发送完毕 */
    // while (USART_GetFlagStatus(USART1, USART_FLAG_TC) != SET);
    return (ch);
}

// 连接在转接板上的总线伺服舵机ID号
uint8_t servoId = 0;
// 状态码
FSUS_STATUS statusCode;

int main (void)
{
    // 嘀嗒定时器初始化
    SysTick_Init();
    // 串口初始化
    Usart_Init();
    // 发送重置用户数据指令
    statusCode = FSUS_ResetUserData(servoUsart, servoId);
    printf("=====reset user data======\r\n status code: %d\r\n", statusCode);
    if (statusCode == FSUS_STATUS_SUCCESS){
        printf("sucess\r\n");
    }else{
        printf("fail\r\n");
    }

    // 死循环
    while (1);
}
```

## 3. 读取用户自定义数[¶](#3 "Permanent link")

读取用户自定义数据 - 读取舵机角度上限

```
#include "stm32f10x.h"
#include "usart.h"
#include "sys_tick.h"
#include "fashion_star_uart_servo.h"

// 使用串口1作为舵机控制的端口
// <接线说明>
// STM32F103 PA9(Tx)  <----> 总线伺服舵机转接板 Rx
// STM32F103 PA10(Rx) <----> 总线伺服舵机转接板 Tx
// STM32F103 GND      <----> 总线伺服舵机转接板 GND
// STM32F103 V5       <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART1_ENABLE为1
// 设置完成之后, 将下行取消注释
Usart_DataTypeDef* servoUsart = &usart1;

// 使用串口2作为日志输出的端口
// <接线说明>
// STM32F103 PA2(Tx) <----> USB转TTL Rx
// STM32F103 PA3(Rx) <----> USB转TTL Tx
// STM32F103 GND     <----> USB转TTL GND
// STM32F103 V5      <----> USB转TTL 5V (可选)
// <注意事项>
// 使用前确保已设置usart.h里面的USART2_ENABLE为1
Usart_DataTypeDef* loggingUsart = &usart2;

// 重定向c库函数printf到串口，重定向后可使用printf函数
int fputc(int ch, FILE *f)
{
    while((loggingUsart->pUSARTx->SR&0X40)==0){}
    /* 发送一个字节数据到串口 */
    USART_SendData(loggingUsart->pUSARTx, (uint8_t) ch);
    /* 等待发送完毕 */
    // while (USART_GetFlagStatus(USART1, USART_FLAG_TC) != SET);
    return (ch);
}

uint8_t servoId = 0;        // 连接在转接板上的总线伺服舵机ID号
FSUS_STATUS statusCode;     // 状态码
float angleLimitHigh = 0;   // 舵机角度上限

int main (void)
{
    // 嘀嗒定时器初始化
    SysTick_Init();
    // 串口初始化
    Usart_Init();

    // 读取用户自定义数据
    // 数据表里面的数据字节长度一般为1个字节/2个字节/4个字节
    // 查阅通信协议可知,舵机角度上限的数据类型是有符号短整型(UShort, 对应STM32里面的int16_t),长度为2个字节
    // 所以这里设置value的数据类型为int16_t
    int16_t value;
    uint8_t dataSize;
    // 传参数的时候, 要将value的指针强行转换为uint8_t
    statusCode = FSUS_ReadData(servoUsart, servoId, FSUS_PARAM_ANGLE_LIMIT_HIGH, (uint8_t *)&value, &dataSize);
    printf("=====read user data======\r\n status code: %d\r\n", statusCode);
    if (statusCode == FSUS_STATUS_SUCCESS){
        // 用户自定义表里面，角度的单位是0.1度. 需要改写为度
        angleLimitHigh = value / 10.0;
        printf("read sucess, angle limit high: %f\r\n", angleLimitHigh);
    }else{
        printf("fail\r\n");
    }

    // 死循环
    while (1);
}
```

**输出日志**

```
=====read user data======
status code: 0
read sucess, angle limit high: 135.000000
```

## 4. 写入用户自定义数据[¶](#4 "Permanent link")

写入用户自定义数据 - 写入舵机角度上限与下限，并开启舵机角度限制开关。

```
#include "stm32f10x.h"
#include "usart.h"
#include "sys_tick.h"
#include "fashion_star_uart_servo.h"

// 使用串口1作为舵机控制的端口
// <接线说明>
// STM32F103 PA9(Tx)  <----> 总线伺服舵机转接板 Rx
// STM32F103 PA10(Rx) <----> 总线伺服舵机转接板 Tx
// STM32F103 GND      <----> 总线伺服舵机转接板 GND
// STM32F103 V5       <----> 总线伺服舵机转接板 5V
// <注意事项>
// 使用前确保已设置usart.h里面的USART1_ENABLE为1
// 设置完成之后, 将下行取消注释
Usart_DataTypeDef* servoUsart = &usart1;

// 使用串口2作为日志输出的端口
// <接线说明>
// STM32F103 PA2(Tx) <----> USB转TTL Rx
// STM32F103 PA3(Rx) <----> USB转TTL Tx
// STM32F103 GND     <----> USB转TTL GND
// STM32F103 V5      <----> USB转TTL 5V (可选)
// <注意事项>
// 使用前确保已设置usart.h里面的USART2_ENABLE为1
Usart_DataTypeDef* loggingUsart = &usart2;

// 重定向c库函数printf到串口，重定向后可使用printf函数
int fputc(int ch, FILE *f)
{
    while((loggingUsart->pUSARTx->SR&0X40)==0){}
    /* 发送一个字节数据到串口 */
    USART_SendData(loggingUsart->pUSARTx, (uint8_t) ch);
    /* 等待发送完毕 */
    // while (USART_GetFlagStatus(USART1, USART_FLAG_TC) != SET);
    return (ch);
}

uint8_t servoId = 0;        // 连接在转接板上的总线伺服舵机ID号
FSUS_STATUS statusCode;     // 状态码
// 限位角度
float angleLimitHigh = 90.0;    // 舵机角度上限 (默认值 135)
float angleLimitLow = -90.0;    // 舵机角度下限 (默认值-135)
uint8_t angleLimitSwitch = 0x01; // 0x01: 开启限位; 0x00: 关闭限位

uint16_t value;
int main (void)
{
    // 嘀嗒定时器初始化
    SysTick_Init();
    // 串口初始化
    Usart_Init();

    // 写入舵机角度上限
    value = (int16_t)(angleLimitHigh*10); // 舵机角度上限 转换单位为0.1度
    statusCode = FSUS_WriteData(servoUsart, servoId, FSUS_PARAM_ANGLE_LIMIT_HIGH, (uint8_t *)&value, 2);
    printf("write angle limit high = %f, status code: %d\r\n", angleLimitHigh, statusCode);

    // 写入舵机角度下限制
    value = (int16_t)(angleLimitLow*10); // 舵机角度下限 转换单位为0.1度
    statusCode = FSUS_WriteData(servoUsart, servoId, FSUS_PARAM_ANGLE_LIMIT_LOW, (uint8_t *)&value, 2);
    printf("write angle limit low = %f, status code: %d\r\n", angleLimitLow, statusCode);

    // 打开舵机角度限位开关, 配置生效
    statusCode = FSUS_WriteData(servoUsart, servoId, FSUS_PARAM_ANGLE_LIMIT_LOW, &angleLimitSwitch, 1);
    printf("enable angle limit mode, status code: %d\r\n", statusCode);

    while (1){
        // 控制舵机角度
        FSUS_SetServoAngle(servoUsart, servoId, 90.0, 2000, 0, 1);
        FSUS_SetServoAngle(servoUsart, servoId, -90.0, 2000, 0, 1);
        // 写入的舵机角度超出了预设的限位, 查看效果
        // FSUS_SetServoAngle(servoUsart, servoId, 135.0, 2000, 0, 1);
        // FSUS_SetServoAngle(servoUsart, servoId, -135.0, 2000, 0, 1);
    }
}
```
