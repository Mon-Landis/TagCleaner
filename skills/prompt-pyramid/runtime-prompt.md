# Prompt Pyramid Runtime

Normalize and order the final diffusion tags using these rules. The caller owns the JSON response wrapper; apply these rules only to the `tags` array.

1. Preserve visually supported character triggers, character identity, copyright identity, subject count, and `solo` before appearance details.
2. Normalize tags to lowercase and use spaces instead of underscores. Strip parenthetical numeric weights while preserving the underlying feature.
3. Remove artist/source strings, duplicate tags, vague beauty terms, and unsupported inventions. Preserve concrete visual features.
4. Order each character block by visual weight:
   - identity, trigger, subject count, `solo`;
   - concrete eye and hair colors;
   - hair length/style, hair structure, facial expression, head/neck accessories;
   - torso clothing from outer layer to inner layer, then lower-body clothing;
   - arm/hand wear, then legwear;
   - footwear;
   - pose, action, and interaction tags.
5. Put background/setting after character blocks and supplied quality/style tags last. Never invent quality tags.
6. Use one canonical tag per feature family. Prefer a visually supported specific colored item over a generic duplicate.
7. Resolve contradictions from the visual analysis. When evidence is insufficient, preserve the safer original rather than guessing.
8. Do not add weights, LoRA blocks, negative prompts, commentary, or Markdown.

For multi-character images, keep identity ownership explicit where the supplied tags permit it. Do not merge one character's color, clothing, or accessories into another character.
