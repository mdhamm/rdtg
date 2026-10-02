# ECU-7 Engine Controller — Software Interface Control Document (Rev B)

This document describes how external equipment talks to the ECU-7 engine controller software.
It is maintained by the controller team and is independent of any test tool.

## 1. Discrete / analog I/O (bench I/O box)

On the bench, the ECU's discrete and analog channels go through an I/O box that is controlled
over **TCP, port 7001**, using a line-based ASCII protocol (`\n` terminated).

| Command | Response | Meaning |
|---|---|---|
| `SET <channel> <number>` | `OK` | Drive an input channel |
| `GET <channel>` | `<number>` | Read any channel |
| `RESET` | `OK` | Power-cycle the ECU (all inputs return to defaults; takes ~10 ms) |
| `LIST` | `ch1 ch2 …` | List the channels |

Unknown channels return `ERR <reason>`.

### 1.1 Channel table

| Channel | Dir | Type | Encoding | Default |
|---|---|---|---|---|
| DI_IGN | in | discrete | 0 = OFF, 1 = ON | 0 |
| DI_FUEL_PUMP_OK | in | discrete | 0 / 1 | 0 |
| DI_MAINT | in | discrete | 0 / 1 (maintenance jumper fitted) | 0 |
| AI_THROTTLE | in | analog | percent, 0.0 – 100.0 | 0 |
| AI_FUEL_PRESS | in | analog | psi, 0.0 – 100.0 | 45 |
| DO_ENG_RUN | out | discrete | 1 = engine running | 0 |
| DO_FUEL_LOW | out | discrete | 1 = low fuel pressure caution | 0 |
| DO_START_INH | out | discrete | 1 = start inhibited | 0 |
| DO_OVERSPEED | out | discrete | 1 = overspeed caution | 0 |
| DO_PUMP_CMD | out | discrete | 1 = fuel pump commanded on | 0 |
| DO_READY | out | discrete | 1 = ready lamp on | 0 |

## 2. MIL-STD-1553 command (bench emulation)

The bench has no real 1553 card. BC→RT receive commands are emulated as **UDP datagrams to
port 7002**. Each datagram is one 1553 transaction, big-endian:

```
word 0   command word:  RT addr (5 bits) | T/R (1 bit, 0 = receive) | subaddress (5) | word count (5)
word 1.. data words (16 bit each)
```

The ECU is **RT 5**. Supported receive subaddresses:

| SA | WC | Name | Data |
|---|---|---|---|
| 3 | 1 | MODE_CMD | word 1: 0 = NORMAL, 1 = MAINT, 2 = TEST |

The ECU applies a mode change after a fixed 40 ms mode-transition period.

## 3. Ethernet status (ENG_STATUS)

The ECU sends status as **UDP to 127.0.0.1:5005 at 50 Hz**. Payload is 8 bytes, big-endian:

| Offset | Size | Field |
|---|---|---|
| 0 | u8 | magic = 0xE5 |
| 1 | u8 | version = 1 |
| 2 | u16 | sequence counter |
| 4 | u16 | flags: bit 0 RPM_VALID, bit 1 MAINT_ACTIVE, bit 2 ENG_RUN |
| 6 | u16 | reserved |

## 4. Behaviour summary (from the ECU design)

- The engine cranks when ignition is ON and the fuel pump is OK, unless the maintenance jumper is
  fitted. Cranking takes 300 ms, after which DO_ENG_RUN = 1.
- When ignition goes OFF (or the fuel pump fails), the engine spins down over 200 ms.
- START_INHIBIT is set when the maintenance jumper is fitted and ignition is ON (30 ms filter).
- OVERSPEED is set while the engine runs with ignition ON and throttle above 95 % (20 ms filter).
- PUMP_CMD follows the ignition switch through a 20 ms relay delay.
- READY is set when ignition is ON and the pump is commanded on (30 ms filter).
- FUEL_LOW is set after fuel pressure has been below 20 psi for 50 ms.
- RPM_VALID is reported while the engine runs with throttle above 20 %.
