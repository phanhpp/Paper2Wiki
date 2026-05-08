---
name: marp-slide
description: Create professional Marp presentation slides with 7 beautiful themes (default, minimal, colorful, dark, gradient, tech, business). Use when users request slide creation, presentations, or Marp documents. Supports custom themes, image layouts, and "make it look good" requests with automatic quality improvements.
---

# Marp Slide Creator

Create professional, visually appealing Marp presentation slides with 7 pre-designed themes and built-in best practices.

Important: this skill belongs to `marp-slide-creator` agent

## Output Location

```bash
SKILL_DIR="${SKILL_DIR:-$(pwd)/skills/marp-slide}"
MARP_SLIDES_DIR="${MARP_SLIDES_DIR:-$(pwd)/marp-slides}"
```

## When to Use This Skill

Use this skill when the user:
- Requests to create presentation slides or Marp documents
- Asks to "make slides look good" or "improve slide design"
- Provides vague instructions like "良い感じにして" (make it nice) or "かっこよく" (make it cool)
- Wants to create lecture or seminar materials
- Needs bullet-point focused slides with occasional images

## Quick Start

### Step 1: Select Theme

First, determine the appropriate theme based on the user's request and content.

**Quick theme selection:**
- **Technical/Developer content** → tech theme
- **Business/Corporate** → business theme
- **Creative/Event** → colorful or gradient theme
- **Academic/Simple** → minimal theme
- **General/Unsure** → default theme
- **Dark background preferred** → dark or tech theme

For detailed theme selection guidance, read `$SKILL_DIR/references/theme-selection.md`.

### Step 2: Create Slides

1. **Read relevant references first**:
   - Always start by reading `$SKILL_DIR/references/marp-syntax.md` for basic syntax
   - For images: `$SKILL_DIR/references/image-patterns.md` (official Marpit image syntax)
   - For advanced features (math, emoji): `$SKILL_DIR/references/advanced-features.md`
   - For custom themes: `$SKILL_DIR/references/theme-css-guide.md`

2. Copy content from the appropriate template file:
   - `$SKILL_DIR/assets/template-basic.md` - Default theme (most common)
   - `$SKILL_DIR/assets/template-minimal.md` - Minimal theme
   - `$SKILL_DIR/assets/template-colorful.md` - Colorful theme
   - `$SKILL_DIR/assets/template-dark.md` - Dark mode theme
   - `$SKILL_DIR/assets/template-gradient.md` - Gradient theme
   - `$SKILL_DIR/assets/template-tech.md` - Tech/code theme
   - `$SKILL_DIR/assets/template-business.md` - Business theme

3. Read `$SKILL_DIR/references/best-practices.md` for quality guidelines

4. Structure content following best practices:
   - Title slide with `<!-- _class: lead -->`
   - Concise h2 titles (5-7 characters in Japanese)
   - 3-5 bullet points per slide
   - Adequate whitespace

5. Add images if needed using patterns from `$SKILL_DIR/references/image-patterns.md`

6. Save to `$MARP_SLIDES_DIR/` with `.md` extension (create the folder if missing)

## Available Themes

### 1. Default Theme
**Colors**: Beige background, navy text, blue headings
**Style**: Clean, sophisticated with decorative lines
**Use for**: General seminars, lectures, presentations
**Template**: `template-basic.md`

### 2. Minimal Theme
**Colors**: White background, gray text, black headings
**Style**: Minimal decoration, wide margins, light fonts
**Use for**: Content-focused presentations, academic talks
**Template**: `template-minimal.md`

### 3. Colorful & Pop Theme
**Colors**: Pink gradient background, multi-color accents
**Style**: Vibrant gradients, bold fonts, rainbow accents
**Use for**: Youth-oriented events, creative projects
**Template**: `template-colorful.md`

### 4. Dark Mode Theme
**Colors**: Black background, cyan/purple accents
**Style**: Dark theme with glow effects, eye-friendly
**Use for**: Tech presentations, evening talks, modern look
**Template**: `template-dark.md`

### 5. Gradient Background Theme
**Colors**: Purple/pink/blue/green gradients (varies per slide)
**Style**: Different gradient per slide, white text, shadows
**Use for**: Visual-focused, creative presentations
**Template**: `template-gradient.md`

### 6. Tech/Code Theme
**Colors**: GitHub-style dark background, blue/green accents
**Style**: Code fonts, Markdown-style headers with # symbols
**Use for**: Programming tutorials, tech meetups, developer content
**Template**: `template-tech.md`

### 7. Business Theme
**Colors**: White background, navy headings, blue accents
**Style**: Corporate presentation style, top border, table support
**Use for**: Business presentations, proposals, reports
**Template**: `template-business.md`

## Creating Slides Process

### Basic Workflow

1. **Understand requirements**
   - Identify content: title, topics, key points
   - Determine target audience
   - Assess formality level

2. **Select theme**
   - Use quick selection rules above
   - If uncertain, consult `$SKILL_DIR/references/theme-selection.md`
   - Default to default theme if still unsure

3. **Apply template**
   - Load appropriate template from `$SKILL_DIR/assets/`
   - CSS is already embedded - no external files needed
   - Maintain template structure

4. **Structure content**
   - Title slide: `<!-- _class: lead -->` + h1
   - Content slides: h2 title + bullet points
   - Keep titles to 5-7 characters (Japanese)
   - Use 3-5 bullet points per slide

5. **Refine quality**
   - Read `$SKILL_DIR/references/best-practices.md`
   - Ensure adequate whitespace
   - Maintain consistency
   - Keep text concise (15-25 chars per line)

6. **Add images**
   - If needed, consult `$SKILL_DIR/references/image-patterns.md`
   - Common: `![bg right:40%](image.png)` for side images
   - Use proper Marp image syntax

7. **Output file**
   - Save to `$MARP_SLIDES_DIR/`
   - Use descriptive filename like `presentation.md`

## Handling "Make It Look Good" Requests

When users give vague instructions like "良い感じにして", "かっこよく", or "make it cool":

1. **Infer theme from content**:
   - Business content → business theme
   - Technical content → tech or dark theme
   - Creative content → gradient or colorful theme
   - General → default theme

2. **Apply best practices automatically**:
   - Shorten titles to 5-7 characters
   - Limit bullet points to 3-5 items
   - Add adequate whitespace
   - Use consistent structure

3. **Enhance visual hierarchy**:
   - Use h3 for sub-sections when appropriate
   - Break up dense text into multiple slides
   - Ensure logical flow (intro → body → conclusion)

4. **Maintain professional tone**:
   - Match formality to content
   - Use parallel structure in lists
   - Keep technical terms consistent

## Image Integration

For slides with images, consult `$SKILL_DIR/references/image-patterns.md` for detailed syntax.

Common patterns:
- **Side image**: `![bg right:40%](image.png)` - Image on right, text on left
- **Centered**: `![w:600px](image.png)` - Centered with specific width
- **Full background**: `![bg](image.png)` - Full-screen background
- **Multiple images**: Multiple `![bg]` declarations

Example lecture pattern:
```markdown
## Slide Title

![bg right:40%](diagram.png)

- Explanation point 1
- Explanation point 2
- Explanation point 3
```

## File Output

Always save the final Marp file to `$MARP_SLIDES_DIR/` with `.md` extension:
- `presentation.md`
- `seminar-slides.md`
- `lecture-materials.md`

## Quality Checklist

Before delivering slides, verify:
- [ ] Theme selected appropriately for content
- [ ] CSS theme is embedded in the file
- [ ] Title slide uses `<!-- _class: lead -->`
- [ ] All h2 titles are concise (5-7 chars)
- [ ] Bullet points are 3-5 items per slide
- [ ] Images use proper Marp syntax
- [ ] File saved under `$MARP_SLIDES_DIR/`
- [ ] Content follows best practices

## References

### Core Documentation
- **Marp syntax**: `$SKILL_DIR/references/marp-syntax.md` - Basic Marp/Marpit syntax (directives, frontmatter, pagination, etc.)
- **Image patterns**: `$SKILL_DIR/references/image-patterns.md` - Official image syntax (bg, filters, split backgrounds)
- **Theme CSS guide**: `$SKILL_DIR/references/theme-css-guide.md` - How to create custom themes based on Marpit specification
- **Advanced features**: `$SKILL_DIR/references/advanced-features.md` - Math, emoji, fragmented lists, Marp CLI, VS Code
- **Official themes**: `$SKILL_DIR/references/official-themes.md` - default, gaia, uncover themes documentation

### Quality & Selection Guides
- **Theme selection**: `$SKILL_DIR/references/theme-selection.md` - How to choose the right theme for content
- **Best practices**: `$SKILL_DIR/references/best-practices.md` - Quality guidelines for "cool" slides

### Templates & Assets
- **Templates**: `$SKILL_DIR/assets/template-*.md` - Starting points with embedded CSS for each theme (7 themes)
- **Standalone CSS**: `$SKILL_DIR/assets/theme-*.css` - CSS files for reference (already embedded in templates)

### Official External Links
- **Marp Official Site**: https://marp.app/
- **Marpit Directives**: https://marpit.marp.app/directives
- **Marpit Image Syntax**: https://marpit.marp.app/image-syntax
- **Marpit Theme CSS**: https://marpit.marp.app/theme-css
- **Marp Core GitHub**: https://github.com/marp-team/marp-core
- **Marp CLI GitHub**: https://github.com/marp-team/marp-cli
