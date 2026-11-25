---
name: motion_editorial
description: Produzir explicadores de tecnologia e negócios com gráficos animados, voz contínua e legendas cinéticas.
---

# Motion Editorial

Use o exemplo [A Origem do QR Code](../../examples/origem_qr_code.json) como estrutura. Defina `fps: 30`, `audio_mode: "continuous"` e `plugin: "motion_editorial"` por cena. Escreva a narração em terceira pessoa. Mantenha kicker e headline curtos para caber no topo. Escolha `barcode_to_qr`, `go_board_markers`, `bar_comparison` ou `open_patent_network` em `visual_type`. Um comparativo recebe duas barras com `label`, `value` e `display`. O badge apresenta o dado mais importante.

## Render

```bash
python -m playwright install --only-shell chromium
python -m core.engine examples/origem_qr_code.json exports/origem_qr_code.mp4 --aspect 9:16 --continuous --alignment exports/origem_qr_code.json
python -m core.engine examples/origem_qr_code.json exports/origem_qr_code_horizontal.mp4 --aspect 16:9 --audio narracao.wav
./render_colab.sh examples/origem_qr_code.json --aspect 9:16 --continuous
```

Use MP3 ou WAV com `--audio`. A voz define cortes e tempos das palavras. O roteiro fornece a grafia da legenda e a palavra falada fica em âmbar. Para a tomada de TTS em PT-BR, o exemplo usa a voz Francisca. Audite a duração, −14 LUFS e quadros do meio e do fim de cada cena. No formato vertical, mantenha o palco com cerca de 88% da largura e a legenda em 74–83% da altura. Em 16:9, preencha `kpi` e `subtitle` na coluna de texto e deixe a direita para o gráfico.

Use `--bgm auto` para a cama procedural ou indique o caminho da trilha. O cartucho usa batidas a até 150 ms da marca original para sincronizar o carimbo e os wipes com o foley, quando a fala deixa uma pausa. `--audio-report <caminho.json>` registra os níveis do AAC, as batidas encontradas e a cadeia aplicada.

## Camadas e render paralelo

Combine `bg_mode` (`grid_glow`, `photo_duotone`, `broll_video`, `radial_dramatic`), `accent` (`amber`, `emerald`, `cyan`, `crimson`, `violet`), `module` e `overlay_fx` (`highlight_bar`, `stamp_badge`, `laser_scan`). Use `metric_badge` como texto do carimbo. Os episódios anteriores continuam aceitando `visual_type` e `broll_dark`.

O FFmpeg prepara `broll_video` com `editorial_teal_amber.cube` antes de compor a tipografia e as legendas. Use `lut` para escolher outro preset. As fotos de gráficos usam `doc_clean_neutral` por padrão.

`subject_depth_title` usa `media_query` para a foto de fundo e o recorte de primeiro plano. A tipografia usa `headline` entre as duas camadas. `data.focus` define a posição do enquadramento de 0 a 1 em cada eixo. A cena usa `editorial_teal_amber` por padrão. [Exemplo de abertura](../../examples/teste_profundidade_editorial.json)

`media_query` recebe uma busca em linguagem natural ou o título exato `File:…` de um item da Wikimedia Commons. Use `media_source` como `auto`, `pixabay` ou `wikimedia`, e `media_orientation` como `horizontal` ou `vertical`. Quando uma busca falha, o cartucho usa o fundo padrão ou exibe uma indicação de imagem indisponível. [API de arquivos da Wikimedia](https://www.mediawiki.org/wiki/API:Imageinfo)

Para vídeo, use `bg_mode: "broll_video"` e `broll_query`. Escolha `broll_source` entre `auto`, `pixabay`, `pexels`, `archive` e `wikimedia`. Uma busca `archive:identificador` fixa o filme. `broll_start_sec` e `broll_duration_sec` selecionam o trecho (padrão de 12 s). Pixabay e Pexels consultam `PIXABAY_API_KEY` e `PEXELS_API_KEY` no ambiente ou em `settings`. O Internet Archive usa itens com licença explícita de domínio público. O cache fica em `.cache/media/` e o render salva os créditos em `<nome>_fontes.md`. [API Pixabay](https://pixabay.com/api/docs/) · [API Pexels](https://www.pexels.com/api/documentation/) · [Uso do acervo Prelinger](https://archivesupport.zendesk.com/hc/en-us/articles/360004715031-Prelinger-Archive)

O FFmpeg compõe o B-roll a 30 fps sob PNGs com transparência. Em fundos opacos, Playwright envia JPEG com qualidade 88. Título e base visual aparecem já no primeiro quadro, com os elementos secundários animados ao longo da cena.

Use `--workers 2` com `python -m core.engine` ou `./render_colab.sh` para fixar dois workers. O padrão é dois no computador local e quatro no Colab. Cada cena web abre uma página independente, gera um trecho de vídeo e a concatenação precede a mixagem da voz contínua.

| Módulo | Opções |
|---|---|
| `kpi_hero` | `data.value`, `unit`, `delta` e `context` |
| `bar_comparison` | `bars` com 2 a 5 entradas `label`, `value` e `display` |
| `line_chart` | `data.points` com 3 a 12 entradas `label`, `value` e `peak` opcional |
| `timeline_steps` | `data.items` com 3 a 5 entradas `date`, `label` e `detail` |
| `split_vs` | `data.items` com duas entradas `date`, `label` e `detail` |
| `process_flow` | `data.items` com 3 a 5 entradas `label` e `detail` |
| `network_nodes` | `data.context` no centro e `data.items` com 4 a 8 entradas `label` e `icon` |
| `checklist_reveal` | `data.items` com 3 a 5 entradas `label` e `detail` |
| `quote_callout` | `data.quote` e `data.author` |
| `news_headline` | `data.source`, `headline`, `excerpt` e `highlight` literal do excerto |
| `photo_spotlight` | `media_query`, `data.caption` e `focus` com coordenadas x e y de 0 a 1 |
| `photo_split_kpi` | `media_query`, `data.caption`, `value`, `unit` e `context` |
| `subject_depth_title` | `media_query`, `headline`, `subtitle` opcional, `data.focus` e `lut` |
| `gauge_meter` | `data.value` de 0 a 100 e `data.context` |
| `terminal_code` | `data.context` e `data.lines` com até oito linhas |

Ícones aceitos em `network_nodes` incluem `smartphone`, `globe`, `credit-card`, `factory`, `ticket`, `utensils`, `package` e `train-front`.

## Composição web

`window.renderAt(tSec, progress, payload)` recebe o tempo local e a fração da duração. Cada cena monta uma timeline GSAP pausada e usa `seek` em segundos. Use o tempo passado pelo adaptador para determinar todo o estado visual. Evite relógio de parede, temporizadores, loops de animação autônomos e aleatoriedade sem semente. Mantenha dependências e assets junto do template. O adaptador serve esses arquivos locais, congela o relógio da página e entrega RGB24 ao encoder existente.

A captura usa o padrão de seek e captura em memória de `packages/engine/src/services/screenshotService.ts` no HyperFrames. A concatenação por trecho segue o padrão de `packages/producer/src/regression-harness-distributed.ts`, revisão `34c7e64`, Apache 2.0. [Licença](../../LICENSES/HYPERFRAMES-APACHE-2.0.txt)

O arquivo `vendor/gsap.min.js` é o bundle original do GSAP 3.14.2 publicado no npm. [Licença do GSAP](https://gsap.com/standard-license/)

Os oito ícones da rede vêm de `lucide-static` 0.468.0. [Licença do conjunto](vendor/LUCIDE-LICENSE)

O QR da demonstração usa correção H. A máscara foi composta para cobrir aproximadamente 30% dos módulos e manter a leitura desse exemplo. O limite de 30% da correção H se refere a palavras-código. [Especificação da Denso Wave](https://www.qrcode.com/en/about/standards.html)
