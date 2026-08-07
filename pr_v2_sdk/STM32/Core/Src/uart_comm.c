/**
 * @file    uart_comm.c
 * @brief   UART communication – interrupt RX, command parser, diagnostic echo.
 *
 *  ★ This file uses the CubeMX-generated global UART handles
 *    (huart1, huart3 declared in usart.h / usart.c).
 *
 *    It does NOT create its own handles.  This ensures the IRQ handlers
 *    in stm32g4xx_it.c (which pass &huart1 / &huart3 to HAL_UART_IRQHandler)
 *    trigger our HAL_UART_RxCpltCallback correctly — because Receive_IT
 *    was started on the SAME handle object that the IRQ handler uses.
 */
#include "uart_comm.h"
#include "usart.h"          /* ← CubeMX 全局 huart1, huart3 */
#include "motor.h"
#include "servo.h"
#include "test.h"

#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdlib.h>

/* ═══════════════════════════════════════════════════════════════════════════
 * Private state
 * ═══════════════════════════════════════════════════════════════════════════ */

/* ── USART1 RX ─────────────────────────────────────────────────────────── */
static uint8_t  u1_rx_byte;
static char     u1_cmd[UART_CMD_BUF_SIZE];
static uint16_t u1_idx;
static volatile uint8_t  u1_ready;
static volatile uint32_t u1_last_rx_tick;
static volatile uint8_t  u1_rx_active;

/* ── USART3 RX ─────────────────────────────────────────────────────────── */
static uint8_t  u3_rx_byte;
static char     u3_cmd[UART_CMD_BUF_SIZE];
static uint16_t u3_idx;
static volatile uint8_t  u3_ready;
static volatile uint32_t u3_last_rx_tick;
static volatile uint8_t  u3_rx_active;

static char tx_buf[256];

/* ── Debug counters ────────────────────────────────────────────────────── */
static volatile uint32_t dbg_u1_rx_count;
static volatile uint32_t dbg_u1_err_count;

/* ── Timeout: 收到字符后 500ms 无换行 → 自动执行 ──────────────────────── */
#define CMD_TIMEOUT_MS  500U

/* Forward declarations */
static void UART_Exec(const char *cmd, uint8_t src);
static void UART_StartReceiveIT_Safe(UART_HandleTypeDef *h,
                                     uint8_t *pData,
                                     volatile uint8_t *active_flag);

/* ═══════════════════════════════════════════════════════════════════════════
 * Init  –  called AFTER MX_USARTx_UART_Init()
 * ═══════════════════════════════════════════════════════════════════════════ */
void UART_Comm_Init(void)
{
    /*
     * GPIO, clock, and UART peripheral init are already done by CubeMX:
     *   MX_USART1_UART_Init()  →  global huart1  (usart.c)
     *   MX_USART3_UART_Init()  →  global huart3  (usart.c)
     *
     * We only need to:
     *  1. Ensure NVIC is enabled for both USARTs
     *  2. Start interrupt-based single-byte reception
     *  3. Print boot banner
     */

    /* ── NVIC — 确保中断使能 ──────────────────────────────────────────── */
    HAL_NVIC_SetPriority(USART1_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(USART1_IRQn);

    HAL_NVIC_SetPriority(USART3_IRQn, 5, 0);
    HAL_NVIC_EnableIRQ(USART3_IRQn);

    /* ── 初始化接收状态 ───────────────────────────────────────────────── */
    u1_idx           = 0;
    u1_ready         = 0;
    u1_rx_active     = 0;
    dbg_u1_rx_count  = 0;
    dbg_u1_err_count = 0;

    u3_idx           = 0;
    u3_ready         = 0;
    u3_rx_active     = 0;

    /* ── 启动中断接收（使用 CubeMX 全局 handle）───────────────────────── */
    UART_StartReceiveIT_Safe(&huart1, &u1_rx_byte, &u1_rx_active);
    UART_StartReceiveIT_Safe(&huart3, &u3_rx_byte, &u3_rx_active);

    /* ── Boot banner ───────────────────────────────────────────────────── */
    UART1_Printf("\r\n=== STM32G474 Motor/Servo Controller ===\r\n");
    UART1_Printf("USART1 (PC Debug) Ready @ %lu baud\r\n",
                 (unsigned long)huart1.Init.BaudRate);
    UART1_Printf("Type HELP for command list.\r\n");
    UART1_Printf("(Send with CR/LF, or wait 500ms auto-execute)\r\n\r\n");

    UART3_Printf("\r\n=== STM32G474 Motor/Servo Controller ===\r\n");
    UART3_Printf("USART3 (Orange Pi) Ready @ %lu baud\r\n",
                 (unsigned long)huart3.Init.BaudRate);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Safe IT receive starter — handles HAL busy / error states
 * ═══════════════════════════════════════════════════════════════════════════ */
static void UART_StartReceiveIT_Safe(UART_HandleTypeDef *h,
                                     uint8_t *pData,
                                     volatile uint8_t *active_flag)
{
    /* 如果 HAL 状态不是 READY，强制复位 */
    if (h->RxState != HAL_UART_STATE_READY) {
        h->RxState = HAL_UART_STATE_READY;
        __HAL_UNLOCK(h);
    }

    HAL_StatusTypeDef rc = HAL_UART_Receive_IT(h, pData, 1);
    if (rc == HAL_OK) {
        *active_flag = 1;
    } else {
        *active_flag = 0;
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Printf helpers  –  all use the CubeMX global handles
 * ═══════════════════════════════════════════════════════════════════════════ */
void UART1_Printf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(tx_buf, sizeof(tx_buf), fmt, ap);
    va_end(ap);
    if (n > 0)
        HAL_UART_Transmit(&huart1, (uint8_t *)tx_buf, (uint16_t)n, 200);
}

void UART3_Printf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(tx_buf, sizeof(tx_buf), fmt, ap);
    va_end(ap);
    if (n > 0)
        HAL_UART_Transmit(&huart3, (uint8_t *)tx_buf, (uint16_t)n, 200);
}

void UART_Printf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(tx_buf, sizeof(tx_buf), fmt, ap);
    va_end(ap);
    if (n > 0) {
        HAL_UART_Transmit(&huart1, (uint8_t *)tx_buf, (uint16_t)n, 200);
        HAL_UART_Transmit(&huart3, (uint8_t *)tx_buf, (uint16_t)n, 200);
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * HAL UART RX complete callback
 *
 *   Called by HAL_UART_IRQHandler() when 1 byte is received.
 *   Because we use the SAME global huart1/huart3 everywhere, the
 *   IRQ handler in stm32g4xx_it.c correctly routes here.
 * ═══════════════════════════════════════════════════════════════════════════ */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1) {
        dbg_u1_rx_count++;

        /* ── 回显每个字节（调试用，确认正常后可删除）──────── */
        HAL_UART_Transmit(&huart1, &u1_rx_byte, 1, 10);

        u1_last_rx_tick = HAL_GetTick();

        if (u1_rx_byte == '\n' || u1_rx_byte == '\r') {
            if (u1_idx > 0 && !u1_ready) {
                u1_cmd[u1_idx] = '\0';
                u1_ready = 1;
            }
            /* 忽略空行 */
        } else if (!u1_ready && u1_idx < UART_CMD_BUF_SIZE - 1) {
            u1_cmd[u1_idx++] = (char)u1_rx_byte;
        }

        /* 重新启动接收 */
        UART_StartReceiveIT_Safe(&huart1, &u1_rx_byte, &u1_rx_active);
    }
    else if (huart->Instance == USART3) {
        u3_last_rx_tick = HAL_GetTick();

        if (u3_rx_byte == '\n' || u3_rx_byte == '\r') {
            if (u3_idx > 0 && !u3_ready) {
                u3_cmd[u3_idx] = '\0';
                u3_ready = 1;
            }
        } else if (!u3_ready && u3_idx < UART_CMD_BUF_SIZE - 1) {
            u3_cmd[u3_idx++] = (char)u3_rx_byte;
        }

        UART_StartReceiveIT_Safe(&huart3, &u3_rx_byte, &u3_rx_active);
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * HAL UART error callback — clear errors and restart reception
 * ═══════════════════════════════════════════════════════════════════════════ */
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1) {
        dbg_u1_err_count++;
        __HAL_UART_CLEAR_FLAG(&huart1, UART_CLEAR_PEF | UART_CLEAR_FEF |
                              UART_CLEAR_NEF | UART_CLEAR_OREF);
        UART_StartReceiveIT_Safe(&huart1, &u1_rx_byte, &u1_rx_active);
    }
    else if (huart->Instance == USART3) {
        __HAL_UART_CLEAR_FLAG(&huart3, UART_CLEAR_PEF | UART_CLEAR_FEF |
                              UART_CLEAR_NEF | UART_CLEAR_OREF);
        UART_StartReceiveIT_Safe(&huart3, &u3_rx_byte, &u3_rx_active);
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Main-loop processing (called from while(1) in main.c)
 * ═══════════════════════════════════════════════════════════════════════════ */
void UART_Comm_Process(void)
{
    uint32_t now = HAL_GetTick();

    /* ── USART1 命令就绪 ───────────────────────────────────────────────── */
    if (u1_ready) {
        UART1_Printf("\r\n");
        UART_Exec(u1_cmd, 1);
        u1_idx   = 0;
        u1_ready = 0;
    }

    /* ── USART1 超时：收到数据但一直没换行 → 自动执行 ──────────────────── */
    if (!u1_ready && u1_idx > 0 && (now - u1_last_rx_tick > CMD_TIMEOUT_MS)) {
        u1_cmd[u1_idx] = '\0';
        UART1_Printf("\r\n[auto-exec]\r\n");
        UART_Exec(u1_cmd, 1);
        u1_idx = 0;
    }

    /* ── USART3 命令就绪 ───────────────────────────────────────────────── */
    if (u3_ready) {
        UART_Exec(u3_cmd, 3);
        u3_idx   = 0;
        u3_ready = 0;
    }

    /* ── USART3 超时 ───────────────────────────────────────────────────── */
    if (!u3_ready && u3_idx > 0 && (now - u3_last_rx_tick > CMD_TIMEOUT_MS)) {
        u3_cmd[u3_idx] = '\0';
        UART_Exec(u3_cmd, 3);
        u3_idx = 0;
    }

    /* ── 如果 RX 中断意外停止，自动恢复 ───────────────────────────────── */
    if (!u1_rx_active) {
        UART_StartReceiveIT_Safe(&huart1, &u1_rx_byte, &u1_rx_active);
    }
    if (!u3_rx_active) {
        UART_StartReceiveIT_Safe(&huart3, &u3_rx_byte, &u3_rx_active);
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Command parser / executor
 * ═══════════════════════════════════════════════════════════════════════════ */
static void UART_Exec(const char *cmd, uint8_t src)
{
    UART1_Printf("[UART%d] CMD: \"%s\"\r\n", src, cmd);

    /* ── HELP ──────────────────────────────────────────────────────────── */
    if (strncmp(cmd, "HELP", 4) == 0 || strncmp(cmd, "help", 4) == 0) {
        UART_Printf(
            "Commands:\r\n"
            "  MA <-100~100>  Motor A speed (A-H)\r\n"
            "  S1 <0~180>     Servo 1 angle (1-4)\r\n"
            "  STOP           Coast all motors\r\n"
            "  BRAKE          Brake all motors\r\n"
            "  TEST           Run test sequence\r\n"
            "  DIAG           Show RX diagnostics\r\n"
            "  HELP           This help text\r\n");
        return;
    }

    /* ── DIAG — 诊断：显示 RX 统计 ────────────────────────────────────── */
    if (strncmp(cmd, "DIAG", 4) == 0 || strncmp(cmd, "diag", 4) == 0) {
        UART1_Printf("USART1 RX bytes: %lu, errors: %lu, active: %d\r\n",
                      (unsigned long)dbg_u1_rx_count,
                      (unsigned long)dbg_u1_err_count,
                      u1_rx_active);
        UART1_Printf("USART1 HAL RxState: 0x%02X\r\n", huart1.RxState);
        return;
    }

    /* ── STOP ──────────────────────────────────────────────────────────── */
    if (strncmp(cmd, "STOP", 4) == 0 || strncmp(cmd, "stop", 4) == 0) {
        Motor_StopAll();
        UART_Printf("OK: All motors stopped (coast)\r\n");
        return;
    }

    /* ── BRAKE ─────────────────────────────────────────────────────────── */
    if (strncmp(cmd, "BRAKE", 5) == 0 || strncmp(cmd, "brake", 5) == 0) {
        for (uint8_t i = 0; i < MOTOR_COUNT; i++)
            Motor_Brake((Motor_ID_t)i);
        UART_Printf("OK: All motors braked\r\n");
        return;
    }

    /* ── TEST ──────────────────────────────────────────────────────────── */
    if (strncmp(cmd, "TEST", 4) == 0 || strncmp(cmd, "test", 4) == 0) {
        Test_RunAll();
        return;
    }

    /* ── Motor command : M<A-H> <speed>  or  m<a-h> <speed> ───────────── */
    if ((cmd[0] == 'M' || cmd[0] == 'm') &&
        ((cmd[1] >= 'A' && cmd[1] <= 'H') || (cmd[1] >= 'a' && cmd[1] <= 'h')))
    {
        uint8_t letter = cmd[1];
        if (letter >= 'a') letter -= 32;
        Motor_ID_t id = (Motor_ID_t)(letter - 'A');

        const char *p = &cmd[2];
        while (*p == ' ') p++;
        int16_t spd = (int16_t)atoi(p);

        if (spd >= -100 && spd <= 100) {
            Motor_SetSpeed(id, spd);
            UART_Printf("OK: Motor %c speed=%d\r\n", letter, spd);
        } else {
            UART_Printf("ERR: Speed out of range (-100~100)\r\n");
        }
        return;
    }

    /* ── Servo command : S<1-4> <angle>  or  s<1-4> <angle> ───────────── */
    if ((cmd[0] == 'S' || cmd[0] == 's') && cmd[1] >= '1' && cmd[1] <= '4') {
        Servo_ID_t id = (Servo_ID_t)(cmd[1] - '1');

        const char *p = &cmd[2];
        while (*p == ' ') p++;
        int angle = atoi(p);

        if (angle >= 0 && angle <= 180) {
            Servo_SetAngle(id, (uint16_t)angle);
            UART_Printf("OK: Servo %c angle=%d\r\n", cmd[1], angle);
        } else {
            UART_Printf("ERR: Angle out of range (0~180)\r\n");
        }
        return;
    }

    UART_Printf("ERR: Unknown command \"%s\". Type HELP\r\n", cmd);
}
