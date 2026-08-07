/**
 * @file    servo.c
 * @brief   PWM servo control – TIM8 CH1-CH4 on PC6-PC9.
 */
#include "servo.h"

static TIM_HandleTypeDef htim_srv;

static const uint32_t srv_ch[SERVO_COUNT] = {
    TIM_CHANNEL_1, TIM_CHANNEL_2, TIM_CHANNEL_3, TIM_CHANNEL_4
};

/* ═══════════════════════════════════════════════════════════════════════════ */

void Servo_Init(void)
{
    /* ── Clock ─────────────────────────────────────────────────────────── */
    __HAL_RCC_TIM8_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();

    /* ── GPIO : PC6-PC9  AF4 (TIM8) ───────────────────────────────────── */
    GPIO_InitTypeDef g = {0};
    g.Pin       = GPIO_PIN_6 | GPIO_PIN_7 | GPIO_PIN_8 | GPIO_PIN_9;
    g.Mode      = GPIO_MODE_AF_PP;
    g.Pull      = GPIO_NOPULL;
    g.Speed     = GPIO_SPEED_FREQ_LOW;
    g.Alternate = GPIO_AF4_TIM8;
    HAL_GPIO_Init(GPIOC, &g);

    /* ── Timer base ────────────────────────────────────────────────────── */
    htim_srv.Instance               = TIM8;
    htim_srv.Init.Prescaler         = SERVO_TIM_PRESCALER;
    htim_srv.Init.CounterMode       = TIM_COUNTERMODE_UP;
    htim_srv.Init.Period            = SERVO_PWM_PERIOD;
    htim_srv.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
    htim_srv.Init.RepetitionCounter = 0;
    htim_srv.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
    HAL_TIM_PWM_Init(&htim_srv);

    /* ── OC channels ───────────────────────────────────────────────────── */
    TIM_OC_InitTypeDef oc = {0};
    oc.OCMode       = TIM_OCMODE_PWM1;
    oc.Pulse        = 1500;   /* centre position 1.5 ms */
    oc.OCPolarity   = TIM_OCPOLARITY_HIGH;
    oc.OCNPolarity  = TIM_OCNPOLARITY_HIGH;
    oc.OCFastMode   = TIM_OCFAST_DISABLE;
    oc.OCIdleState  = TIM_OCIDLESTATE_RESET;
    oc.OCNIdleState = TIM_OCNIDLESTATE_RESET;

    for (uint8_t i = 0; i < SERVO_COUNT; i++) {
        HAL_TIM_PWM_ConfigChannel(&htim_srv, &oc, srv_ch[i]);
        HAL_TIM_PWM_Start(&htim_srv, srv_ch[i]);
    }
}

void Servo_SetAngle(Servo_ID_t servo, uint16_t angle)
{
    if ((uint8_t)servo >= SERVO_COUNT) return;
    if (angle > 180) angle = 180;

    uint16_t pulse = (uint16_t)(SERVO_PULSE_MIN_US +
                     (uint32_t)(SERVO_PULSE_MAX_US - SERVO_PULSE_MIN_US) * angle / 180U);
    __HAL_TIM_SET_COMPARE(&htim_srv, srv_ch[servo], pulse);
}

void Servo_SetPulseUs(Servo_ID_t servo, uint16_t pulse_us)
{
    if ((uint8_t)servo >= SERVO_COUNT) return;
    if (pulse_us < SERVO_PULSE_MIN_US) pulse_us = SERVO_PULSE_MIN_US;
    if (pulse_us > SERVO_PULSE_MAX_US) pulse_us = SERVO_PULSE_MAX_US;

    __HAL_TIM_SET_COMPARE(&htim_srv, srv_ch[servo], pulse_us);
}
