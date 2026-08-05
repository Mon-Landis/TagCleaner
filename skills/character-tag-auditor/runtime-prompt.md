# Character Tag Auditor Runtime

Audit every supplied original tag against the attached A-pose reference for exactly one locked character.

Locked trigger: {{locked_trigger}}
Audit mode: {{audit_mode}}
Reference description: {{reference_description}}
Original tags: {{original_tags}}

Use exactly one category per tag: `identity`, `hair`, `eyes`, `face`, `body`, `clothing`, `footwear`, `legwear`, `wearable_accessory`, `action`, `pose`, `expression`, `scene`, `composition`, `quality`, `object`, or `other`.

Return every original tag exactly once and do not alter its `tag` value. Use one decision:

- `keep`: correct and already useful.
- `delete`: incorrect, conflicting, redundant, or non-core appearance/wearable detail.
- `replace`: visually correct but imprecise; provide one nonempty canonical `replacement_tag` different from the source.
- `uncertain`: reference evidence is insufficient; do not modify it.

Hard boundaries:

- The locked trigger is always `keep`, `include_in_prompt: true`, and `prompt_order: 0`.
- Identity, action, pose, expression, scene, composition, quality, object, other-person evidence, and other protected tags must remain `keep`; normally set `include_in_prompt: false`.
- Only hair, eyes, face, body appearance, clothing, footwear, legwear, and wearable accessories may be deleted or replaced.
- Never invent colors or features. Prefer `uncertain` when occluded or ambiguous.
- A replacement target is one tag without commas, line breaks, weights, or control characters. It cannot equal its source and cannot form a replacement chain.
- Preserve confirmed concrete hair colors. Generic multicolor structure never replaces a concrete color.
- When color is clearly visible, prefer one color-prefixed garment/accessory/footwear tag over generic duplicates.
- Treat other characters' traits as protected dataset evidence with `include_in_prompt: false`.

In `sparse` mode, retain only core stable identity, concrete eye/hair traits, signature garments, footwear, legwear, and wearable accessories. Remove non-core bangs, small face/hair details, generic ornament categories, fabric/material/trim details, and redundant generic garments. Target roughly 8-16 prompt tags.

In `full` mode, keep all visually confirmed appearance, garment, material, trim, small hair/face, and worn-accessory details; delete only incorrect or conflicting details.

Assign `prompt_order` in this sequence: locked trigger; subject count/solo; eye color; hair color/length/structure; hair accessories; headwear; face/ear/neck accessories; upper-body clothing; one-piece/lower-body clothing; arm/hand wear; legwear; footwear.

Return only this JSON object:

{"decisions":[{"tag":"original tag","category":"hair","decision":"keep","replacement_tag":"","include_in_prompt":true,"prompt_order":3,"reason":"brief visual reason"}],"character_prompt":["locked trigger","1girl","concrete feature"]}

`character_prompt` must contain only canonical, visually confirmed locked-character tags in prompt order. Do not include protected scene/action/pose/expression/composition/quality/object/other-person tags.
