# 账户收益通行证轨道版生成记录

版本 V0.3。使用内置 imagegen，根据 V0.2 业务组件板中的 06 区域重新设计独立通行证组件。保留原图，将新版作为通行证外观的后续评审稿；参数保存在账户数据配置中，主页面只展示可滚轮浏览的进度轨道与里程碑。图片展示静态效果，真实动效待界面原型阶段验证。

## 生成提示

```text
Use case: ui-mockup / precise-object-edit.
Input image: the existing A-trade business component sheet is an EDIT TARGET for section 06 only and a palette reference. The user rejected section 06. Redesign that component completely and present ONLY the redesigned section 06 as an enlarged standalone image. Omit sections 05,07,08 from this deliverable. It is a UI design revision, not a new app or a device photograph.

Primary request: a BEAUTIFUL MINIMAL horizontally scrollable ACCOUNT-RETURN MILESTONE PASS. The main screen should be only a lovely progress rail and milestone badges. The user explicitly rejected all lower level-range boxes, all level detail cards, the right parameter form, and excessive interface chrome. Those must be entirely removed. Target return and total stages are still configurable in underlying account data, but none of those inputs belong in this visible component.

Composition: wide landscape about 3:1, high resolution, front-facing single component close-up. One refined near-white panoramic viewport with thin cool-grey outline and 12px corners on a very pale #F5F7FB background. Minimal dark navy heading "账户收益通行证" at top left. Small unobtrusive text at top right "滚轮浏览历程". Tiny outside-corner version label "06 · V0.3". Plenty of breathing room but keep the actual component compact, around 200–240 logical pixels tall in the imagined desktop. NO poster grid and NO surrounding browser or desktop frame.

Visual design: an elegant continuous slender pearl-grey rail across the width, not a plain onboarding stepper. Completed portion is a luminous cobalt-to-sky-blue inset ribbon, with a narrow traveling highlight frozen in the still image. A very delicate soft shadow gives the rail depth; use the #2457E5 brand blue. Six or seven spacious milestone landmarks hover just above or intersect the rail:
- completed landmarks have exquisitely crafted small faceted blue crystal/metal emblems, subtle silver edges, simple star/diamond/ascending-line embossed motifs, restrained dimensional shading, as if premium achievement pins designed for a professional desktop app;
- the current landmark is slightly larger, a refined blue glass crest with a thin partial halo and a small precise glow at the progress frontier;
- future landmarks are beautiful pale etched silver-blue outlines, subtly visible, without loud padlocks;
- a major milestone farther ahead has an elegant laurel/star outline to suggest a larger achievement, with no reward box or game currency.
Keep motifs coherent and restrained. Avoid clumsy flat circles containing huge numbers, cheap cartoon medals, oversized trophies, neon bloom, saturated gold, purple gaming UI, excessive 3D, confetti or fantasy scenery. This should feel calm, polished, and desirable within a light financial workstation.

The progress rail continues beyond the viewport. Fade its very left and right edges into the background, clipping partial edge milestones to communicate a long horizontally scrollable journey. The illustrated fill frontier should remain near the current milestone, not fill all future landmarks. Milestone identifiers may appear as tiny plain text underneath (e.g. 01,02,03,04,05,06) with NO frames around them. Use at most one small "当前" label near the active landmark, with elegant typography. Below the rail only one tiny quiet hint is allowed: "滚轮或拖动，浏览完整历程". Do not show a numeric target return or a total stage count.

Motion intent expressed as a still: one frozen highlight on the completed rail, a gentle halo around the active badge, tasteful depth suggesting lift-on-hover. This is a static design image, no claim of real animation. No motion storyboard boxes, no timing table, no carousel dots, no chapters, no tabs, no 01–10 / 11–20 boxes, no "第4阶段" panel, no curve or stat cards, no parameter sidebar, no "设置目标" or "展开全部" button, no percentages, no editable fields, no settings form.

Preserve A-trade light theme and clear blue action/achievement language. The output must visually answer the user's aesthetic rejection with a materially more elegant single horizontal pass rail. All elements should look feasible as layered UI and badge artwork, not a photorealistic physical object.
```
