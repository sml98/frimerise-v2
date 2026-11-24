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

## Composição web

`window.renderAt(tSec, progress, payload)` recebe o tempo local e a fração da duração. Cada cena monta uma timeline GSAP pausada e usa `seek` em segundos. Use o tempo passado pelo adaptador para determinar todo o estado visual. Evite relógio de parede, temporizadores, loops de animação autônomos e aleatoriedade sem semente. Mantenha dependências e assets junto do template. O adaptador serve esses arquivos locais, congela o relógio da página e entrega RGB24 ao encoder existente.

A captura usa o padrão de seek e captura em memória de `packages/engine/src/services/screenshotService.ts` no HyperFrames, revisão `34c7e64`, Apache 2.0. [Licença](../../LICENSES/HYPERFRAMES-APACHE-2.0.txt)

O arquivo `vendor/gsap.min.js` é o bundle original do GSAP 3.14.2 publicado no npm. [Licença do GSAP](https://gsap.com/standard-license/)

Os oito ícones da rede vêm de `lucide-static` 0.468.0. [Licença do conjunto](vendor/LUCIDE-LICENSE)

O QR da demonstração usa correção H. A máscara foi composta para cobrir aproximadamente 30% dos módulos e manter a leitura desse exemplo. O limite de 30% da correção H se refere a palavras-código. [Especificação da Denso Wave](https://www.qrcode.com/en/about/standards.html)
