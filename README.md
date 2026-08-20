# 🏥 WARD NO. 9 

![Version](https://img.shields.io/badge/version-ALPHA__1-orange.svg)
![Python](https://img.shields.io/badge/Made_with-Python_%26_Pygame-blue.svg)
![Status](https://img.shields.io/badge/status-In%20Development-success.svg)

**WARD NO. 9** is a hardcore first-person indie survival horror with stealth elements, procedural audio, and deep AI. The player must escape an abandoned psychiatric hospital, evading a creature that reacts not only to in-game actions but also to sounds from the real world.

Created by: **Lonewolf239**

---

## 🌟 Features

Unlike simple walking simulators, Ward No. 9 features complex survival mechanics:

- **Microphone as a Gameplay Element (MicListener):** The game analyzes input from your real-life microphone. Any loud noise in reality will draw the monster to your hiding spot.
- **Multi-level Creature AI:** The monster features `Patrol`, `Investigate`, `Stalk`, and `Hunt` states. It can break down doors, spot your flashlight beam from afar, and methodically search lockers if it senses you are nearby.
- **Sanity and Stamina System:** Darkness and the enemy's presence drain your sanity, triggering panic effects like a racing heartbeat. Sprinting saves your life, but your stamina is strictly limited.
- **Procedural Audio:** Most of the terrifying sounds, ambient drones, heartbeats, and footsteps are procedurally generated using mathematical waves (sine waves, white noise, filters), creating a unique and oppressive sound design.
- **Diverse Levels:** The game includes 3 stages: The Upper Floor (gathering fuses), The Blood Basement (finding valves), and The Courtyard (finding bolt cutters).

---

## 🚀 Installation & Launch

The project is built using an automatic builder. You do not need to install Python or any dependencies manually if you just want to play.

1. Go to the [Releases](../../releases) section or the [itch.io]([link_to_your_itch](https://lonewolf239.itch.io/ward-no-9)) page.
2. Download the archive for your OS (`Windows`, `macOS`, or `Linux`).
3. Extract the archive into any folder.
4. Run the `ward9` executable.

> **For Developers:**
> If you want to run the game from the source code, make sure you have Python 3.8+ installed along with the dependencies from `requirements.txt` (including `pygame`, `numpy`, `sounddevice`).

---

## 🎮 Controls

| Key | Action |
| :--- | :--- |
| **W, A, S, D** | Movement |
| **Shift** | Sprint (consumes stamina, highly audible) |
| **Ctrl** | Crouch (silent movement, allows hiding behind cover) |
| **F** | Toggle flashlight (drains battery, attracts AI) |
| **E** | Interact (open doors, hide in lockers, pick up items) |
| **Mouse** | Look (Pitch & Yaw) |

---

## 🛠 Development Status

Current version: **ALPHA_1**

**Implemented:**
- [x] Basic player controller (walking, sprinting, stealth).
- [x] Monster AI (sight, hearing, memory, breaking doors).
- [x] Microphone integration (`pactl` / `sounddevice`).
- [x] 3 unique stages with different objectives and visual styles.
- [x] Custom procedural sound engine.

---

## 📜 License

© 2026 Lonewolf239. All rights reserved.
