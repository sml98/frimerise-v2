---
name: cutout-paper
description: Dirigir episódios editoriais em colagem de papel recortado no FrimeRise, com rigs em stop motion, mapa, carimbos e foley em 16:9 ou 9:16.
---

# Cut-out Paper

Use o cartucho para histórias, mapas e curiosidades que ganham clareza com uma mesa editorial. Consulte [A Guerra dos Emus](../../examples/guerra_dos_emus.json) para o formato do episódio.

- Use `niche_skill: "cutout_paper"`, `plugin: "cutout_paper"` em cada cena e vídeo a 30 fps. Escolha `layout` entre `map`, `field`, `expedition`, `routes`, `portrait` e `custom`. Defina `paper` como `kraft` ou `newsprint`.
- Planeje a duração pela narração. Em português, escreva em terceira pessoa e use `tts_locale: "pt-BR"` com uma voz nativa. Para peças históricas, atribua estimativas e resultados aos registros que sustentam os números.
- Use `headline` e `label` para orientar o leitor. Reserve `stamp` para um dado ou virada e marque sua chegada com `stamp_at_sec`. Acione `paper_slide`, `stamp_hit`, `paper_rustle`, `paper_pop` ou `camera_click` em `foley`. O impacto encaixa na grade de 12 fps e seu som usa o mesmo quadro de apresentação.
- Em 9:16, o cartucho usa 7–18% da altura para títulos, 19–26% para carimbos, 26–66% para a ação e 72–81% para legendas. Use `caption_numbers` para trocar expressões faladas por algarismos na legenda, com o início de cada tomada preservado.
- Para arte própria, use `media_plugin: "local_pack"`. Declare caminhos PNG/SVG em `media` e referencie-os em `cutouts` por `asset`. Cada peça aceita `layer` (`bg`, `mid`, `fg`), `x`, `y`, `width` em fração do lado curto, `motion`, `delay_sec` e `rotation`. Valide a leitura no retrato e mantenha os elementos essenciais à esquerda da área dos controles.
- Renderize com `python -m cartridges.cutout_paper examples/guerra_dos_emus.json exports/guerra_dos_emus.mp4 --aspect 9:16 --inspect exports/guerra_dos_emus_inspecao`. Troque para `--aspect 16:9` para a versão horizontal. Confira a auditoria AAC/H.264 e os quatro quadros de inspeção.
- Para render a partir de uma locução existente, use `python -m cartridges.cutout_paper examples/guerra_dos_emus.json exports/emus.mp4 --audio narracao.wav --alignment exports/emus.json`. Para gerar a tomada inteira, use `--continuous --tts edge_kokoro_tts --voice pt-BR-AntonioNeural` no lugar de `--audio`. No modo contínuo, as legendas vêm do alinhamento e o carimbo pode usar `stamp_at_word` ou `stamp_at_progress`.
- Em uma entrega autorizada no Colab, registre a revisão Git e use `./render_colab.sh examples/guerra_dos_emus.json --aspect 9:16`. A L4 é o padrão. Selecione a T4 com `FRIMERISE_COLAB_GPU=T4`. As credenciais ficam fora do Git.

## Colagem de revista

Escolha `module` nas cenas fotográficas e continue usando `layout` nas cenas ilustradas. A câmera e a legenda seguem a saída de 30 fps, com poses e traços a 12 fps. [Exemplo com fotos e animação](../../examples/teste_colagem_revista.json)

- `photo_cutout` usa `media_query`, `media_source`, `media_orientation` opcional e `photo_label`. Para escolher uma foto específica do Commons, use `media_query: "File:Nome da foto.jpg"` e `media_source: "wikimedia"`.
- `newspaper_clipping` usa os mesmos campos de foto e aceita `publication`, `clipping_headline`, `clipping_text` e `highlight`. Use `clipping_headline` para o texto principal. Identifique uma reprodução editorial quando a página for composta pelo roteiro.
- `collage_board` usa `photos` com duas ou três entradas contendo `media_query`, `media_source`, `media_orientation` e `label`. `board_stamp` é o texto do carimbo que fecha a sequência.
- `lut` aceita os nomes em `assets/luts/`, com ou sem `.cube`. O padrão das fotos deste cartucho é `vintage_paper_warm`. As fontes da mídia ficam no arquivo `*_fontes.md`.
