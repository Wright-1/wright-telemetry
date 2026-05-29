# Wright Telemetry Collector — Design System

> PyQt6 desktop application. Derived from the Wright One portal design language, adapted for native widgets.

---

## Color System

**Strategy: Restrained.** Tinted neutrals with a single blue accent used sparingly for interactive elements (toggles, primary buttons, active nav).

All colors specified in hex for PyQt compatibility. Tinted toward the Wright One brand blue-gray.

### Named Tokens

| Token | Hex | Usage |
|---|---|---|
| `bg-window` | `#FAFBFC` | Main window background |
| `bg-sidebar` | `#F3F4F6` | Left navigation panel |
| `bg-card` | `#FFFFFF` | Content cards, permission rows |
| `bg-card-hover` | `#F9FAFB` | Hovered permission row |
| `bg-security` | `#1A1D23` | Security profile panel (dark) |
| `border-default` | `#E5E7EB` | Card borders, dividers |
| `border-subtle` | `#F0F1F3` | Inner dividers |
| `text-primary` | `#111318` | Headings, primary labels |
| `text-secondary` | `#4B5563` | Descriptions, secondary copy |
| `text-muted` | `#9CA3AF` | Hints, version labels |
| `text-on-dark` | `#FFFFFF` | Text on dark surfaces |
| `text-on-dark-muted` | `#9CA3AF` | Secondary text on dark surfaces |
| `accent-blue` | `#3B82F6` | Toggle on-state, primary button, active nav |
| `accent-blue-hover` | `#2563EB` | Primary button hover |
| `accent-green` | `#22C55E` | Security checkmark, enabled indicators |
| `accent-red` | `#EF4444` | Error-category icon tint |
| `accent-orange` | `#F97316` | Warning-category icon tint |
| `accent-purple` | `#8B5CF6` | Config-category icon tint |

### Permission Category Colors (left border accents)

| Category | Color | Metrics |
|---|---|---|
| Sensor data | `#3B82F6` | Temperature & Fan RPM, Hashrate & Power Stats |
| Reliability | `#22C55E` | Uptime & Firmware Info |
| Hardware | `#8B5CF6` | Per-Hashboard Chip Temps |
| Errors | `#EF4444` | Miner Errors |
| System | `#F97316` | Automatic Updates |
| Remote | `#3B82F6` | Remote Configuration |

---

## Typography

### Font Family

**Roboto** — consistent with the Wright One portal. Load system Roboto or bundle.

Fallback stack: `Roboto, -apple-system, Segoe UI, sans-serif`

### Type Scale

| Role | Size (px) | Weight | Usage |
|---|---|---|---|
| Page heading | 22 | 600 | "Welcome to Wright Telemetry" |
| Page description | 13 | 400 | Subheading under page title |
| Section heading | 11 | 700 | "SECURITY PROFILE" (uppercase, tracked) |
| Permission title | 14 | 600 | "Temperature & Fan RPM" |
| Permission description | 12 | 400 | "Reads sensors to predict lifespan." |
| Permission detail | 12 | 400 | Expanded description text |
| Nav item | 13 | 500 | Sidebar navigation labels |
| Nav header | 14 | 700 | "Setup" |
| Nav subheader | 11 | 400 | "Configuration Wizard" |
| Button label | 13 | 600 | "Next: Discover Miners" |
| Version label | 11 | 400 | "v0.7.3 / LOCAL INSTANCE" |
| Body small | 12 | 400 | Helper text, links |

---

## Layout

### Window

- Default size: 1060 × 720
- Minimum size: 900 × 600
- Three-column layout: sidebar (180px fixed) | main content (flexible) | security panel (240px fixed)

### Sidebar

- Fixed 180px width
- Top: "Setup" header + "Configuration Wizard" subheader
- Nav items with icon + label, 40px row height
- Active item: light blue background tint (`#EBF5FF`), blue text
- Bottom: version + "LOCAL INSTANCE" label

### Main Content

- Padded 32px all sides
- Permission rows: full-width cards with 3px left border (category color)
- Expandable rows: chevron rotates, detail text appears below
- Bottom bar: help link left, Cancel + Next buttons right

### Security Panel

- Fixed 240px width, dark background
- Padded 20px
- Lock icon + "SECURITY PROFILE" header
- Encryption status row with green checkmark
- GitHub repo card with "View Code" button

---

## Components

### Permission Row

- 3px left border in category color
- Icon (16×16) + title (semibold) + toggle + chevron
- Subtitle below title in muted text
- Expanded state: description paragraph below, indented

### Toggle Switch

- 40×22px track
- Off: `#D1D5DB` track, white thumb
- On: `#3B82F6` track, white thumb

### Navigation Item

- Icon (16×16) + label
- Default: `text-secondary`
- Active: blue text, `#EBF5FF` background, left 3px blue border
- Hover: `#F3F4F6` background

### Primary Button

- Background: `#3B82F6`, text: white
- Border radius: 8px
- Padding: 10px 24px
- Hover: `#2563EB`
- Right arrow icon on "Next" actions

### Secondary Button

- Background: white, border: `#E5E7EB`, text: `#374151`
- Border radius: 8px
- Padding: 10px 24px

---

## Motion

None. This is a native PyQt application. No animations beyond default widget transitions.

---

## Do / Don't

| Do | Don't |
|---|---|
| Use system-native scroll behavior | Custom scrollbars |
| Keep permission descriptions concise | Multi-paragraph permission explanations in the list view |
| Show security panel on every page | Hide security info behind a menu |
| Use the category color system consistently | Random colors on permission icons |
| Match the portal's Roboto typography | Introduce new typefaces |
