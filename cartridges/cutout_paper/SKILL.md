---
name: cutout-paper
description: Dirigir episódios editoriais em colagem de papel recortado no FrimeRise, com rigs em stop motion, mapa, carimbos e foley em 16:9 ou 9:16.
---

# Cut-out Paper

Use o cartucho para histórias, mapas e curiosidades que ganham clareza com uma mesa editorial. Consulte [A Guerra dos Emus](../../examples/guerra_dos_emus.json) para o formato do episódio.

- Use `niche_skill: "cutout_paper"`, `plugin: "cutout_paper"` em cada cena e vídeo a 30 fps. Escolha `layout` entre `map`, `field`, `expedition`, `routes`, `portrait` e `custom`. Defina `paper` como `kraft` ou `newsprint`.
- Planeje a duração pela narração. Em português, escreva em terceira pessoa e use `tts_locale: "pt-BR"` com uma voz nativa. Para peças históricas, atribua estimativas e resultados aos registros que sustentam os números.
- Use `headline` e `label` para orientar o leitor. Reserve `stamp` para um dado ou virada e marque sua chegada com `stamp_at_sec`. Acione `paper_slide`, `stamp_hit`, `paper_rustle`, `paper_pop` ou `camera_click` em `foley`. O impacto e seu som usam o mesmo quadro a 30 fps.
- Em 9:16, o cartucho usa 7–18% da altura para títulos, 19–26% para carimbos, 26–66% para a ação e 72–81% para legendas. Use `caption_numbers` para trocar expressões faladas por algarismos na legenda, com o início de cada tomada preservado.
- Para arte própria, use `media_plugin: "local_pack"`. Declare caminhos PNG/SVG em `media` e referencie-os em `cutouts` por `asset`. Cada peça aceita `layer` (`bg`, `mid`, `fg`), `x`, `y`, `width` em fração do lado curto, `motion`, `delay_sec` e `rotation`. Valide a leitura no retrato e mantenha os elementos essenciais à esquerda da área dos controles.
- Renderize com `python -m cartridges.cutout_paper examples/guerra_dos_emus.json exports/guerra_dos_emus.mp4 --aspect 9:16 --inspect exports/guerra_dos_emus_inspecao`. Troque para `--aspect 16:9` para a versão horizontal. Confira a auditoria AAC/H.264 e os quatro quadros de inspeção.
- Para render a partir de uma locução existente, use `python -m cartridges.cutout_paper examples/guerra_dos_emus.json exports/emus.mp4 --audio narracao.wav --alignment exports/emus.json`. Para gerar a tomada inteira, use `--continuous --tts edge_kokoro_tts --voice pt-BR-AntonioNeural` no lugar de `--audio`. No modo contínuo, as legendas vêm do alinhamento e o carimbo pode usar `stamp_at_word` ou `stamp_at_progress`.
- Em uma entrega autorizada no Colab, registre a revisão Git e use `./render_colab.sh examples/guerra_dos_emus.json --aspect 9:16`. A L4 é o padrão. Selecione a T4 com `FRIMERISE_COLAB_GPU=T4`. As credenciais ficam fora do Git.

## Colagem de revista

Escolha `module` nas cenas fotográficas e continue usando `layout` nas cenas ilustradas. Rigs, câmera, traços e legenda seguem a saída de 30 fps. A borda de papel pode variar a 12 Hz. [Exemplo com fotos e animação](../../examples/teste_colagem_revista.json)

- `photo_cutout` usa `media_query`, `media_source`, `media_orientation` opcional e `photo_label`. Para escolher uma foto específica do Commons, use `media_query: "File:Nome da foto.jpg"` e `media_source: "wikimedia"`.
- `newspaper_clipping` usa os mesmos campos de foto e aceita `publication`, `clipping_headline`, `clipping_text` e `highlight`. Use `clipping_headline` para o texto principal. Identifique uma reprodução editorial quando a página for composta pelo roteiro.
- `collage_board` usa `photos` com duas ou três entradas contendo `media_query`, `media_source`, `media_orientation` e `label`. `board_stamp` é o texto do carimbo que fecha a sequência.
- Para montar uma gravura ou um mapa inteiro em papel, use `layout: "custom"` e `media_query`. Para trocar a imagem por um arquivo local, acrescente `media_file` ao plano.
- Em `newspaper_clipping`, mantenha foto e texto em regiões distintas. O modo dinâmico mede as caixas de conteúdo no Chromium.
- `lut` aceita os nomes em `assets/luts/`, com ou sem `.cube`. O padrão das fotos deste cartucho é `vintage_paper_warm`. As fontes da mídia ficam no arquivo `*_fontes.md`.
- `media_file` pode apontar para um PNG, JPG ou MP4 e tem prioridade sobre `media_query`. Uma cena sem `module` trata a foto local como `photo_cutout` e o MP4 como um cartão de vídeo a 30 fps. Os módulos de fotografia usam um quadro representativo quando a fonte é MP4. Em `collage_board`, `photos` também aceita `media_file` por peça.

## Montagem dinâmica

- Defina `dynamic_edit: true` para auditar diversidade de cor, ritmo, repetição de imagens e área segura. Declare `show_header: true` em até 30% das cenas. O padrão é oculto.
- Use `action_cutout_puppet` com `setting` (`graveyard_night`, `village_road`, `archive_desk`, `open_field`, `city_street`) e `action` (`walk_to_target`, `inspect_with_lantern`, `dig_or_open`, `flee_run`, `crowd_gather`). Forneça recortes reais em `media_file`, `media_query` ou `photos`. O BiRefNet recorta imagens opacas. Informe uma peça por personagem.
- Use `full_bleed_macro` com mídia, `focus_x` e `focus_y` normalizados e `mark: "circle"` ou `"underline"`. A ampliação parte de 2,2×. Em `dark_archive`, use `label` para identificação, `photo_label` para o contexto e `lantern_focus: true` quando a luz precisar percorrer a gravura. `evidence_pan` requer duas imagens em `photos`.
- Use `photo_25d_parallax` para dar profundidade a fotos e gravuras de acervo. Indique `media_file` ou `media_query`, identificação em `headline` e crédito curto em `photo_label`. O cartucho usa DA3 e MI-GAN antes de animar a câmera.
- Reserve `impact_slam` a cenas de 2–3,5 s medidas. Use `hold_impact_through_sentence: true` com `visual_anchor` quando o cartão de abertura precisar durar até 5,5 s para acompanhar a frase. Use `impact_text` para a cifra ou palavra e deixe o módulo suprimir a legenda.
- Em suspense histórico, use `tone: "historical_suspense"` ou `"dark_mystery"`. A voz Edge padrão desse perfil é Antonio, com `pitch=-6Hz` e `rate=-4%`. Declare `pronunciation_overrides` no episódio para resolver a pronúncia mantendo a grafia original nas legendas.
- Escreva em `visual_anchor` um trecho literal da fala e em `visual_description` a ação que a imagem deve mostrar. Para duas cenas manuais consecutivas com o mesmo módulo e ações distintas, declare `allow_consecutive_module: true` na segunda cena e descreva a diferença visual.
- Use `transition` e `music_mood` por cena. `cut_on_beat` só move o corte quando há uma batida dentro da pausa. `drop_silence` zera só a trilha, mantendo voz e efeitos.
- `broll_clip` aceita um MP4 local ou um objeto com `query`, `description`, `source` e `duration_sec`. A composição usa FFmpeg e o LUT escolhido. Use `cartridge_override` quando a cena deve escolher outro cartucho.
- A oclusão das camadas segue `styles/crayon-book/demo/rig.js` do Lemo-Opuscar, revisão `8ca0dd8`, código MIT. A captura por seek usa o adaptador derivado de `packages/engine/src/services/screenshotService.ts` do HyperFrames, revisão `34c7e64`, Apache-2.0. [Referência do rig](https://github.com/lemomo-ai/lemo-opuscar/blob/8ca0dd8/styles/crayon-book/demo/rig.js) · [Licença do adaptador](../../LICENSES/HYPERFRAMES-APACHE-2.0.txt)

## Erros Proibidos (Lições Aprendidas)

- Use pessoas e animais de acervo no módulo de recortes documentais. Bonecos feitos com primitivas SVG são proibidos. Mantenha a pílula escura com texto off-white e destaque âmbar sobre fac-símiles e páginas carregadas.
- Coloque fita só no canto de um papel retangular com bounding box livre de pessoas, animais e outros recortes. Use o checkpoint de matting comercial aprovado e deduplique os créditos. Mantenha cada carimbo na faixa reservada no retrato.
- Comece com arte visível e use novas ações a cada 2,5–3 s em cenas acima de 5 s. Encerre a fala da cena em `.`, `!` ou `?`. Confira legendas com duas ou mais palavras, métricas de áudio e ausência de janelas mortas no relatório e no `qa_grid.png`. [Regras completas](../../AGENTS.md#erros-proibidos-lições-aprendidas)
