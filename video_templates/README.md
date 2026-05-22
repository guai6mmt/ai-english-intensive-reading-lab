# Video Templates

These templates control the HTML frames used for video export.

Edit these files, then export the video package again:

- `h3_16x9.html` controls the horizontal 16:9 layout.
- `v3_9x16.html` controls the vertical 9:16 layout.

The most common CSS knobs are:

- `.sentence-en` for English sentence size and line height.
- `.sentence-cn` for Chinese translation size and line height.
- `.main` or `.sentence` for spacing between English and Chinese.
- `.notes`, `.vocab`, and `.vocab-grid` for vocabulary layout.
- `.title-bar` if the title takes too much space.

Do not remove placeholders such as `{{ sentence_en_html }}`, `{{ sentence_cn }}`,
`{{ vocab_html }}`, `{{ en_size }}`, or `{{ cn_size }}` unless you also update
`video_render.py`.

If a template file is missing, the app falls back to the built-in minimal layout.
