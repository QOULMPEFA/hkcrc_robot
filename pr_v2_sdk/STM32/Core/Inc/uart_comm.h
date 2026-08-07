/**
 * @file    uart_comm.h
 * @brief   USART1 (PC / CH341 debug) and USART3 (Orange Pi) communication.
 *
 *  Uses CubeMX-generated global huart1 / huart3 (from usart.h).
 *  Call UART_Comm_Init() AFTER MX_USART1_UART_Init() / MX_USART3_UART_Init().
 *
 *  Command protocol (text, terminated by \n or \r):
 *    MA <speed>      Motor A speed  (-100 ~ +100)
 *    …
 *    MH <speed>      Motor H speed
 *    S1 <angle>      Servo 1 angle  (0 ~ 180)
 *    …
 *    S4 <angle>      Servo 4 angle
 *    STOP            Coast all motors
 *    BRAKE           Brake all motors
 *    TEST            Run test sequence
 *    DIAG            Show RX diagnostics
 *    HELP            Print help text
 */
#ifndef UART_COMM_H
#define UART_COMM_H

#ifdef __cplusplus
extern "C" {
#endif

#include "stm32g4xx_hal.h"

#define UART_CMD_BUF_SIZE   64U

/**
 * @brief  Enable NVIC, start interrupt RX, print banner.
 *         Must be called AFTER MX_USARTx_UART_Init().
 */
void UART_Comm_Init(void);

/** Call from the main super-loop. */
void UART_Comm_Process(void);

/** printf-style output to USART1 only (PC debug). */
void UART1_Printf(const char *fmt, ...);

/** printf-style output to USART3 only (Orange Pi). */
void UART3_Printf(const char *fmt, ...);

/** printf-style output to BOTH UARTs. */
void UART_Printf(const char *fmt, ...);

#ifdef __cplusplus
}
#endif

#endif /* UART_COMM_H */
