---
name: Liquid Spatial
colors:
  surface: '#121316'
  surface-dim: '#121316'
  surface-bright: '#38393c'
  surface-container-lowest: '#0d0e11'
  surface-container-low: '#1b1b1f'
  surface-container: '#1f1f23'
  surface-container-high: '#292a2d'
  surface-container-highest: '#343538'
  on-surface: '#e3e2e6'
  on-surface-variant: '#c1c6d7'
  inverse-surface: '#e3e2e6'
  inverse-on-surface: '#2f3034'
  outline: '#8b90a0'
  outline-variant: '#414755'
  surface-tint: '#adc6ff'
  primary: '#adc6ff'
  on-primary: '#002e69'
  primary-container: '#4b8eff'
  on-primary-container: '#00285c'
  inverse-primary: '#005bc1'
  secondary: '#e8b3ff'
  on-secondary: '#510074'
  secondary-container: '#7508a5'
  on-secondary-container: '#e19fff'
  tertiary: '#53e16f'
  on-tertiary: '#003911'
  tertiary-container: '#00a741'
  on-tertiary-container: '#00320e'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#d8e2ff'
  primary-fixed-dim: '#adc6ff'
  on-primary-fixed: '#001a41'
  on-primary-fixed-variant: '#004493'
  secondary-fixed: '#f6d9ff'
  secondary-fixed-dim: '#e8b3ff'
  on-secondary-fixed: '#310048'
  on-secondary-fixed-variant: '#7201a2'
  tertiary-fixed: '#72fe88'
  tertiary-fixed-dim: '#53e16f'
  on-tertiary-fixed: '#002107'
  on-tertiary-fixed-variant: '#00531c'
  background: '#121316'
  on-background: '#e3e2e6'
  surface-variant: '#343538'
typography:
  display-xl:
    fontFamily: Plus Jakarta Sans
    fontSize: 56px
    fontWeight: '700'
    lineHeight: 64px
    letterSpacing: -0.03em
  display-xl-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 42px
    letterSpacing: -0.025em
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 38px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 26px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: -0.005em
  body-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0em
  body-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: 0.005em
  label-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.02em
  caption:
    fontFamily: Plus Jakarta Sans
    fontSize: 10px
    fontWeight: '500'
    lineHeight: 12px
    letterSpacing: 0.025em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1rem
  gutter-desktop: 1.5rem
  margin: 1rem
  margin-tablet: 1.5rem
  margin-desktop: 2.5rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2.25rem
---

## Brand & Style

This design system draws direct inspiration from Apple macOS Sequoia, iOS 18, and visionOS spatial design paradigms. It communicates quiet confidence, industrial precision, and optic fluidity. Built for modern high-performance interfaces, productivity suites, and spatial experiences, it replaces opaque flat surfaces with multi-tiered physical depth, optical light refraction, and dimensional clarity.

### Personality & Emotional Response
- **Restrained Brilliance:** Surfaces remain muted, velvety, and translucent, allowing content to take center stage while interactive elements emit an authoritative, luminous presence.
- **Optic Weightlessness:** Through calibrated multi-stop blurs and layered luminance, interfaces feel suspended in physical space rather than printed on glass.
- **Tactile Precision:** Interactive controls evoke CNC-milled aluminum, sapphire lens rings, and polished micro-beveled glass.

### Design Movement
**Spatial Glassmorphism & Refined Minimal Modernism.** Unlike exaggerated 2020 glassmorphism or high-contrast cyberpunk HUDs, this aesthetic emphasizes optical realism: 
- Subtly tiered glass materials with back-surface dispersion.
- Fine specular edge reflections (`rgba(255, 255, 255, 0.12)` inner keylines).
- Iridescent specular accents (subtle Siri-inspired cyan-purple-magenta gradients) reserved exclusively for elevated focus states and generative actions.

## Colors

The palette operates in a default dark spatial environment, balancing deep obsidian blacks with cool, neutral aluminum grayscale steps and hyper-pure optical accents.

### Palette Architecture
- **Primary Accent (`#007AFF` System Blue):** The principal visual anchor for primary actions, active selection indicators, and focused inputs.
- **Secondary Accent (`#AF52DE` Purple):** Works alongside `#00C7BE` and `#FF2D55` within iridescent micro-gradients for artificial intelligence states, generative flows, and spatial navigation anchors.
- **Tertiary Accent (`#34C759` Emerald Green):** Communicates active system health, secure connection tunnels, and success states.
- **Base Canvas (`#0A0B0D` to `#121316` Obsidian):** An ultra-deep, neutral void with 2% blue bias to eliminate dead pixel blackness and maximize optical transmission of blurred layers.

### Dynamic Material Stack (CSS Tokens)
- **Ultra-Thin Material:** `rgba(255, 255, 255, 0.05)` backdrop-filter: `blur(40px) saturate(190%)`.
- **Regular Surface Material:** `rgba(28, 29, 34, 0.65)` backdrop-filter: `blur(50px) saturate(180%)`.
- **Thick Spatial Plate:** `rgba(38, 40, 48, 0.78)` backdrop-filter: `blur(64px) saturate(160%)`.
- **Specular Keyline Border:** Linear gradient from `rgba(255, 255, 255, 0.18)` at 0% light angle to `rgba(255, 255, 255, 0.04)` at 100%.

## Typography

The typography mirrors the structural purity and optical legibility of Apple's SF Pro using **Plus Jakarta Sans**. Characterized by tall x-heights, humanist apertures, and refined geometry, it renders crisply across low-luminosity displays and ultra-dense retina viewports.

### Rules of Engagement
- **Tracking & Kern:** Negative tracking is required on all levels at or above 18px to preserve spatial cohesion. Compact titles convey architectural stability.
- **Hierarchy Tiers:** Primary headers utilize bold and semi-bold weights with tight line heights. Informational labels and metadata shift toward medium weights with slightly expanded tracking for effortless skimming.
- **Rendering Standards:** Always set `-webkit-font-smoothing: antialiased` and `-moz-osx-font-smoothing: grayscale`. Text on dynamic glass backgrounds must include a faint drop shadow (`0 1px 2px rgba(0, 0, 0, 0.45)`) when rendered over fluid light sources.

## Layout & Spacing

The layout is built upon an 8-point spatial base with a 4-point sub-grid for delicate micro-alignments (such as icon-to-label centering and segmented control tabs).

### Grid Framework
- **Desktop (>= 1200px):** 12-column adaptive grid with `2.5rem` (`40px`) margins and `1.5rem` (`24px`) gutters. Sidebars pin to translucent floating panels separated from the primary window canvas by `1rem`.
- **Tablet (768px - 1199px):** 8-column layout with `1.5rem` margins and `1rem` gutters. Master-detail views compress to slide-over translucent modal sheets.
- **Mobile (< 768px):** 4-column layout with `1rem` margins and `0.75rem` gutters. Bottom navigation is docked in an ergonomic floating pill elevated `12px` above the home indicator.

### Dynamic Canvas Behaviors
- **Negative Space:** Dense content cards are nested within expansive peripheral margins, preserving the unhurried, gallery-like posture of visionOS windowing.
- **Floating Separation:** Structural zones (headers, bottom action strips, inspector bars) do not dock to screen edges with blunt dividers; instead, they float with uniform perimeter offsets (`margin: space-md`).

## Elevation & Depth

Visual hierarchy does not rely on opaque drop shadows; instead, it is driven by optical thickness, background light diffusion, specular keylines, and soft ambient light traps.

### Elevation Layers
1. **Level 0 (Canvas Void):** Deep solid neutral ground (`#0A0B0D`). Zero blur.
2. **Level 1 (Docked Shells & Panels):** Subtle translucent fill `rgba(22, 24, 29, 0.65)` with `blur(32px)`, bordered by a continuous 1px keyline `rgba(255, 255, 255, 0.08)`.
3. **Level 2 (Floating Cards & Modals):** Medium glass `rgba(32, 35, 44, 0.70)` with `blur(48px)`, accompanied by dual ambient shadows:
   - Ambient dispersion: `0 20px 48px -12px rgba(0, 0, 0, 0.6)`.
   - Contact rim: `0 0 0 1px rgba(255, 255, 255, 0.12) inset`.
4. **Level 3 (Interactive Controls & Menus):** High-luminance glass `rgba(45, 50, 64, 0.85)` with `blur(60px)` and active top specular highlight:
   - Highlight: `inset 0 1px 0 0 rgba(255, 255, 255, 0.22)`.
   - Outer glow: `0 8px 24px -4px rgba(0, 0, 0, 0.5)`.

### Light Direction & Edge Physics
Every floating card features a directional gradient border simulating a 45-degree overhead light source. The top and left borders reflect fine specularity, while the bottom and right borders bleed softly into the underlying dark backdrop.

## Shapes

The geometric signature is defined by Apple-style continuous curvature (superellipses / squircles). Sharp 90-degree corners and harsh, circular pill distortions on standard cards are avoided in favor of gentle, fluid silhouettes.

### Corner Radii Guidelines
- **Base Components (Inputs, Buttons, Chips):** `0.5rem` (`8px`) to `0.75rem` (`12px`).
- **Segmented Sliders & Island Controls:** `1rem` (`16px`) up to fully pill-shaped (`9999px`) where the height is strictly capped below `40px`.
- **Panels, Sheets, and Glass Slabs:** `1.25rem` (`20px`) to `1.5rem` (`24px`).
- **Nesting Formula:** When nesting surfaces, inner border-radius equals outer border-radius minus interior padding (`R_inner = R_outer - Padding`), guaranteeing concentric curves without visual pinch.

## Components

### Buttons
- **Primary:** Apple System Blue (`#007AFF`) base overlaid with an inner specular top rim: `inset 0 1px 0 rgba(255, 255, 255, 0.28)`. Text is pure white, semibold. On hover, luminance increases by 8% without scale shift.
- **Glass / Secondary:** `rgba(255, 255, 255, 0.08)` surface fill with `backdrop-filter: blur(20px)` and an inner 1px ring `rgba(255, 255, 255, 0.12)`. On hover, surface shifts to `rgba(255, 255, 255, 0.14)`.
- **Iridescent (AI / Spatial Actions):** Border gradient transitioning smoothly through `#00C7BE`, `#AF52DE`, and `#FF2D55`. Surface retains deep translucent glass with text rendered in clear white.

### Segmented Controls
- A recessed track in `rgba(0, 0, 0, 0.35)` with `padding: 3px` and `border-radius: 12px`.
- The active sliding thumb uses `rgba(255, 255, 255, 0.14)` with `backdrop-filter: blur(24px)`, a crisp `0.5px` border (`rgba(255, 255, 255, 0.2)`), and an ambient lift shadow: `0 3px 8px rgba(0, 0, 0, 0.35)`.
- Transitions utilize spring mechanics (`damping: 28`, `stiffness: 300`).

### Cards & Glass Platters
- Composed of translucent tiers with content padding of `1.5rem` (`space-lg`).
- No solid interior divide lines. Content sections within a platter are segmented using background luminance shifts (`rgba(255, 255, 255, 0.03)`) or negative space rather than hard horizontal rules.

### Input Fields
- Understated default state: `rgba(255, 255, 255, 0.04)` fill with an inset border of `rgba(255, 255, 255, 0.08)`. Height is standardized at `40px` with `12px` horizontal padding.
- Focused state: Border transitions to `#007AFF` accompanied by an external diffuse bloom: `0 0 0 3.5px rgba(0, 122, 255, 0.25)`.

### Switches & Checkboxes
- Toggle switch conforms to the classic Apple geometry (`51px` width by `31px` height). Off state: neutral track `rgba(255, 255, 255, 0.16)`. On state: solid `#34C759` or `#007AFF`. The thumb is bright porcelain `#FFFFFF` with a directional micro drop shadow.

### Chips & Badges
- Capsule pills with `font-size: 12px`, `font-weight: 500`, and `padding: 4px 10px`.
- Status indicators pair a 6px glowing LED dot with matching colored translucent glass background (`rgba(52, 199, 89, 0.15)` for operational, `rgba(255, 149, 0, 0.15)` for degraded).