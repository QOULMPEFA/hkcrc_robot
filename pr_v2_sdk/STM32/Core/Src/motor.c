/**
 * @file    motor.c
 * @brief   DC Motor control – AT8236 H-bridge, PWM via TIM1/2/3/5/15.
 */
#include "motor.h"

/* ═══════════════════════════════════════════════════════════════════════════
 * Timer handles (private)
 * ═══════════════════════════════════════════════════════════════════════════ */
static TIM_HandleTypeDef htim_m1;   /* TIM1  – Motor A, B */
static TIM_HandleTypeDef htim_m2;   /* TIM2  – Motor C, D */
static TIM_HandleTypeDef htim_m3;   /* TIM3  – Motor E, G */
static TIM_HandleTypeDef htim_m5;   /* TIM5  – Motor H    */
static TIM_HandleTypeDef htim_m15;  /* TIM15 – Motor F    */

/* ═══════════════════════════════════════════════════════════════════════════
 * Per-motor configuration table
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef struct {
    TIM_HandleTypeDef *htim;
    uint32_t ch_in1;   /* IN1 channel (forward PWM)  */
    uint32_t ch_in2;   /* IN2 channel (reverse PWM)  */
} Motor_Cfg_t;

static const Motor_Cfg_t mcfg[MOTOR_COUNT] = {
    [MOTOR_A] = { &htim_m1,  TIM_CHANNEL_1, TIM_CHANNEL_2 },
    [MOTOR_B] = { &htim_m1,  TIM_CHANNEL_3, TIM_CHANNEL_4 },
    [MOTOR_C] = { &htim_m2,  TIM_CHANNEL_1, TIM_CHANNEL_2 },
    [MOTOR_D] = { &htim_m2,  TIM_CHANNEL_3, TIM_CHANNEL_4 },
    [MOTOR_E] = { &htim_m3,  TIM_CHANNEL_1, TIM_CHANNEL_2 },
    [MOTOR_F] = { &htim_m15, TIM_CHANNEL_2, TIM_CHANNEL_1 },  /* FIN1=CH2, FIN2=CH1 */
    [MOTOR_G] = { &htim_m3,  TIM_CHANNEL_3, TIM_CHANNEL_4 },
    [MOTOR_H] = { &htim_m5,  TIM_CHANNEL_1, TIM_CHANNEL_2 },
};

/* ═══════════════════════════════════════════════════════════════════════════
 * GPIO initialisation
 * ═══════════════════════════════════════════════════════════════════════════ */
static void Motor_GPIO_Init(void)
{
    GPIO_InitTypeDef g = {0};

    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();

    g.Mode  = GPIO_MODE_AF_PP;
    g.Pull  = GPIO_NOPULL;
    g.Speed = GPIO_SPEED_FREQ_HIGH;

    /* TIM1 CH1-CH4 : PC0-PC3 (AF2) */
    g.Pin       = GPIO_PIN_0 | GPIO_PIN_1 | GPIO_PIN_2 | GPIO_PIN_3;
    g.Alternate = GPIO_AF2_TIM1;
    HAL_GPIO_Init(GPIOC, &g);

    /* TIM2 CH1-CH4 : PA0-PA3 (AF1) */
    g.Pin       = GPIO_PIN_0 | GPIO_PIN_1 | GPIO_PIN_2 | GPIO_PIN_3;
    g.Alternate = GPIO_AF1_TIM2;
    HAL_GPIO_Init(GPIOA, &g);

    /* TIM3 CH1-CH2 : PA6, PA7 (AF2) */
    g.Pin       = GPIO_PIN_6 | GPIO_PIN_7;
    g.Alternate = GPIO_AF2_TIM3;
    HAL_GPIO_Init(GPIOA, &g);

    /* TIM3 CH3-CH4 : PB0, PB1 (AF2) */
    g.Pin       = GPIO_PIN_0 | GPIO_PIN_1;
    g.Alternate = GPIO_AF2_TIM3;
    HAL_GPIO_Init(GPIOB, &g);

    /*
     * TIM5 CH1 : PB2 (AF2)
     * NOTE: Verify the AF number against CubeMX / datasheet for your revision.
     */
    g.Pin       = GPIO_PIN_2;
    g.Alternate = GPIO_AF2_TIM5;
    HAL_GPIO_Init(GPIOB, &g);

    /*
     * TIM5 CH2 : PC12 (AF1)
     * NOTE: Verify the AF number against CubeMX / datasheet for your revision.
     */
    g.Pin       = GPIO_PIN_12;
    g.Alternate = GPIO_AF1_TIM5;
    HAL_GPIO_Init(GPIOC, &g);

    /* TIM15 CH1-CH2 : PB14, PB15 (AF1) */
    g.Pin       = GPIO_PIN_14 | GPIO_PIN_15;
    g.Alternate = GPIO_AF1_TIM15;
    HAL_GPIO_Init(GPIOB, &g);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Timer + OC channel initialisation helper
 *   ch_mask : bit 0 = CH1, bit 1 = CH2, bit 2 = CH3, bit 3 = CH4
 * ═══════════════════════════════════════════════════════════════════════════ */
static void Motor_TIM_Init(TIM_HandleTypeDef *htim,
                            TIM_TypeDef       *instance,
                            uint8_t            ch_mask)
{
    htim->Instance               = instance;
    htim->Init.Prescaler         = 0;
    htim->Init.CounterMode       = TIM_COUNTERMODE_UP;
    htim->Init.Period            = MOTOR_PWM_PERIOD;
    htim->Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
    htim->Init.RepetitionCounter = 0;
    htim->Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
    HAL_TIM_PWM_Init(htim);

    TIM_OC_InitTypeDef oc = {0};
    oc.OCMode       = TIM_OCMODE_PWM1;
    oc.Pulse        = 0;
    oc.OCPolarity   = TIM_OCPOLARITY_HIGH;
    oc.OCNPolarity  = TIM_OCNPOLARITY_HIGH;
    oc.OCFastMode   = TIM_OCFAST_DISABLE;
    oc.OCIdleState  = TIM_OCIDLESTATE_RESET;
    oc.OCNIdleState = TIM_OCNIDLESTATE_RESET;

    static const uint32_t ch_lut[4] = {
        TIM_CHANNEL_1, TIM_CHANNEL_2, TIM_CHANNEL_3, TIM_CHANNEL_4
    };

    for (uint8_t i = 0; i < 4; i++) {
        if (ch_mask & (1U << i)) {
            HAL_TIM_PWM_ConfigChannel(htim, &oc, ch_lut[i]);
            HAL_TIM_PWM_Start(htim, ch_lut[i]);   /* also enables MOE for adv. timers */
        }
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API
 * ═══════════════════════════════════════════════════════════════════════════ */

void Motor_Init(void)
{
    /* Enable timer clocks */
    __HAL_RCC_TIM1_CLK_ENABLE();
    __HAL_RCC_TIM2_CLK_ENABLE();
    __HAL_RCC_TIM3_CLK_ENABLE();
    __HAL_RCC_TIM5_CLK_ENABLE();
    __HAL_RCC_TIM15_CLK_ENABLE();

    Motor_GPIO_Init();

    Motor_TIM_Init(&htim_m1,  TIM1,  0x0F);  /* CH1-4 */
    Motor_TIM_Init(&htim_m2,  TIM2,  0x0F);  /* CH1-4 */
    Motor_TIM_Init(&htim_m3,  TIM3,  0x0F);  /* CH1-4 */
    Motor_TIM_Init(&htim_m5,  TIM5,  0x03);  /* CH1-2 */
    Motor_TIM_Init(&htim_m15, TIM15, 0x03);  /* CH1-2 */

    Motor_StopAll();
}

void Motor_SetSpeed(Motor_ID_t motor, int16_t speed)
{
    if ((uint8_t)motor >= MOTOR_COUNT) return;

    if (speed >  100) speed =  100;
    if (speed < -100) speed = -100;

    const Motor_Cfg_t *c = &mcfg[motor];
    uint32_t abs_spd = (uint32_t)(speed >= 0 ? speed : -speed);
    uint32_t duty    = (MOTOR_PWM_PERIOD + 1U) * abs_spd / 100U;

    if (speed > 0) {
        /* Forward: IN1 = PWM, IN2 = 0 */
        __HAL_TIM_SET_COMPARE(c->htim, c->ch_in1, duty);
        __HAL_TIM_SET_COMPARE(c->htim, c->ch_in2, 0);
    } else if (speed < 0) {
        /* Reverse: IN1 = 0, IN2 = PWM */
        __HAL_TIM_SET_COMPARE(c->htim, c->ch_in1, 0);
        __HAL_TIM_SET_COMPARE(c->htim, c->ch_in2, duty);
    } else {
        __HAL_TIM_SET_COMPARE(c->htim, c->ch_in1, 0);
        __HAL_TIM_SET_COMPARE(c->htim, c->ch_in2, 0);
    }
}

void Motor_Brake(Motor_ID_t motor)
{
    if ((uint8_t)motor >= MOTOR_COUNT) return;
    const Motor_Cfg_t *c = &mcfg[motor];
    __HAL_TIM_SET_COMPARE(c->htim, c->ch_in1, MOTOR_PWM_PERIOD + 1U);
    __HAL_TIM_SET_COMPARE(c->htim, c->ch_in2, MOTOR_PWM_PERIOD + 1U);
}

void Motor_Coast(Motor_ID_t motor)
{
    if ((uint8_t)motor >= MOTOR_COUNT) return;
    const Motor_Cfg_t *c = &mcfg[motor];
    __HAL_TIM_SET_COMPARE(c->htim, c->ch_in1, 0);
    __HAL_TIM_SET_COMPARE(c->htim, c->ch_in2, 0);
}

void Motor_StopAll(void)
{
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        Motor_Coast((Motor_ID_t)i);
    }
}
