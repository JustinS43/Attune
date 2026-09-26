/*
 * Board differences in one place, so the sketch builds for both boards:
 *   - Arduino UNO R3   (ATmega328P, arduino:avr:uno)        2 KB RAM, 32 KB flash
 *   - Arduino UNO R4 WiFi (RA4M1, arduino:renesas_uno:unor4wifi)
 *
 * On AVR, string literals and constant tables live in RAM unless they are put in flash
 * with PROGMEM and read back with pgm_read_*. The RIG_* macros below do that on AVR and
 * turn into plain reads on the R4, where constants are already in flash.
 */
#pragma once
#include <Arduino.h>
#include <string.h>

#if defined(ARDUINO_ARCH_AVR)
#define RIG_AVR 1
#elif defined(ARDUINO_ARCH_RENESAS) || defined(ARDUINO_UNOR4_WIFI)
#define RIG_AVR 0
#else
#error "Attune rig firmware targets the UNO R3 (arduino:avr:uno) or the UNO R4 WiFi (arduino:renesas_uno:unor4wifi)"
#endif

// Only the UNO R4 WiFi has the 12x8 LED matrix; every other board shows status on LED 13.
#if defined(ARDUINO_UNOR4_WIFI)
#define RIG_HAS_MATRIX 1
#else
#define RIG_HAS_MATRIX 0
#endif

#if RIG_AVR
#include <avr/pgmspace.h>
#define RIG_PROGMEM PROGMEM
#define RIG_READ_U8(addr) pgm_read_byte(addr)
#define RIG_READ_U16(addr) pgm_read_word(addr)
#define RIG_READ_PTR(addr) ((const void *)pgm_read_word(addr))  // AVR pointers are 16 bit
#define RIG_MEMCPY_P(dst, src, n) memcpy_P((dst), (src), (n))
#define RIG_STRCMP_P(ram, flash) strcmp_P((ram), (flash))        // flash = a RIG_PROGMEM string
#define RIG_IS(ram, literal) (strcmp_P((ram), PSTR(literal)) == 0)  // literal stays in flash
#else
#define RIG_PROGMEM
#define RIG_READ_U8(addr) (*(const uint8_t *)(addr))
#define RIG_READ_U16(addr) (*(const uint16_t *)(addr))
#define RIG_READ_PTR(addr) (*(const void *const *)(addr))
#define RIG_MEMCPY_P(dst, src, n) memcpy((dst), (src), (n))
#define RIG_STRCMP_P(ram, flash) strcmp((ram), (flash))
#define RIG_IS(ram, literal) (strcmp((ram), (literal)) == 0)
#endif
