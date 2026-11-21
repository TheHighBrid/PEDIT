# Classic typography verification

The approved visual canvas remains the default. All editable exports are
clearly revised copies; source metadata, signed-document protections and
source artwork must remain intact.

## Current verified cases

- Original 36 FONTFIX tests, including rotated orthogonal text, embedded font
  aliases and native text extraction.
- Fresh neutral draft tests for black and colored vertical text, existing
  bold/italic, French accents, embedded-font symbols, literal character
  spacing, decoration lines, and HTTP upload/inspect/edit/download.
- Remote access, authentication, startup failure and origin lifecycle tests.

## Pending quality gates

1. Arabic and right-to-left: test logical Unicode extraction, contextual glyph
   shaping, ligatures and combining marks, mixed RTL/LTR layout, punctuation,
   font coverage and selectability independently. Until the shaping path is
   validated, keep the explicit refusal and do not claim support.
2. Tracking: distinguish actual PDF character positioning from literal spaces.
   Verify both native extraction and glyph positions. Do not promise tracking
   preservation in the classic engine until this passes.
3. Underline and strikethrough: preserve source artwork and annotations;
   determine whether decorations are text-associated or independent page
   graphics. Existing drawn lines remain; changes in length require review.
4. Expand fixtures with neutral, authorized multilingual drafts and multiple
   renderers. Keep original user documents out of the public test corpus.
