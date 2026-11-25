# FrimeRise v2

O FrimeRise valida o JSON de episódio, mede a narração em samples e fecha a duração das cenas antes da renderização. O cartucho principal, `crayon_book`, anima ilustrações a giz com narração e legendas manuscritas. Consulte o [roteiro de implementação](MASTER_SPEC_V2.md).

## Testes locais

Use Python 3.12, a biblioteca de sistema Cairo para rasterizar SVG e uma instalação de FFmpeg com `ffprobe` e `libx264` disponíveis no `PATH`.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
```

Os testes usam narração e frames gerados localmente. O teste de integração passa pelo pipe FFmpeg, masteriza e audita o MP4 e extrai três PNGs em um diretório temporário.

## Demo Crayon Book

O demo reconta a história da Arca em terceira pessoa, com locução em PT-BR. Cada página abre com traços visíveis e completa o desenho progressivo em 0,9 s. Câmera, personagens e objetos se movem a 30 fps. O boil mantém a textura da cera a 12 Hz. Em cenas com personagens, a Arca mede 2,2 vezes a altura visível do maior deles. `wash: "rain"` ou `wash: "stars"` revela marcas de cera branca sob a aquarela. `wash_at_word` alinha a entrada da aquarela à narração. A duração é definida pela fala medida e pelas pausas de cada cena. [Referências e créditos](assets/README.md#crayon-book)

```bash
python -m core.engine episodes/demo_crayon.json exports/demo_crayon.mp4 --keyframes exports/demo_crayon_keyframes
python -m plugins.qc.ffprobe_audit exports/demo_crayon.mp4 --episode episodes/demo_crayon.json
```

O formato `16:9` usa 1920 × 1080. Para Shorts e Reels, defina `aspect_ratio: "9:16"`, `width: 1080` e `height: 1920` no JSON. O cartucho recompõe a ilustração e encaixa títulos e legendas na área segura comum aos dois formatos.

## Direção e entrega no Colab

Use a [skill Crayon Book](skills/crayon_book/SKILL.md) e os [presets](skills/crayon_book/presets.json) para dirigir um episódio. Faça commit do JSON e dos assets que ele usa e envie a revisão ao remoto Git autorizado.

```bash
FRIMERISE_DB=/caminho/frimerise.db ./render_colab.sh episodes/demo_crayon.json
```

O script usa `colab` autenticado e a configuração do rclone em `~/.config/rclone/rclone.conf`. Use `RCLONE_CONFIG` para outro caminho e `FRIMERISE_RCLONE_REMOTE` para escolher outro remote (padrão `gdrive`). Quando o episódio usa Gemini TTS, a base SQLite `settings(key, value)` fornece a `GEMINI_API_KEY`. O job recebe só essa entrada da base. As credenciais ficam fora do Git.

A sessão `frime` importa a revisão comprometida de um Git bundle, verifica a L4 e exige `h264_nvenc`. Depois da auditoria técnica, o rclone publica e confere o MP4 e os três PNGs em `FrimeRise_Exports/<slug>/`. A sessão encerra ao final ou em caso de falha.

## Cut-out Paper

O segundo cartucho faz colagens editoriais em Python com recortes e câmera a 30 fps, três planos de paralaxe e foley procedural. O exemplo da Guerra dos Emus usa cinco cenas narradas em terceira pessoa em PT-BR. [Skill e opções de cena](cartridges/cutout_paper/SKILL.md) · [Fontes históricas e créditos](assets/README.md#cut-out-paper)

```bash
python -m cartridges.cutout_paper examples/guerra_dos_emus.json exports/guerra_dos_emus.mp4 --aspect 9:16 --inspect exports/guerra_dos_emus_inspecao
python -m cartridges.cutout_paper examples/guerra_dos_emus.json exports/guerra_dos_emus_horizontal.mp4 --aspect 16:9
./render_colab.sh examples/guerra_dos_emus.json --aspect 9:16
```

O launcher do Colab aceita episódios de `episodes/` ou `examples/` com `--aspect` opcional. Use `FRIMERISE_COLAB_GPU=T4` para selecionar a T4. O render local usa a seleção de encoder do núcleo.

## Render e auditoria

Cada render produz `<slug>_qc/qa_grid.png` e `<slug>_qc/relatorio_qc_blindagem.json` ao lado do vídeo. O gate avalia o AAC final, a duração, o primeiro quadro, a pontuação da fala por cena, as legendas e a variação visual sem contar a faixa de legenda. Em cenas acima de 5 s, as janelas avaliadas e suas falhas são registradas em segundos. Para repetir a inspeção usando o relatório existente, rode `python -m core.qc_gate exports/<slug>.mp4`.

### Áudio Único Contínuo

```bash
# Uma tomada de TTS com as cenas descritas no episódio
python -m core.engine examples/guerra_dos_emus.json exports/emus.mp4 --continuous --tts edge_kokoro_tts --voice pt-BR-FranciscaNeural --alignment exports/emus.json

# Áudio externo sem decupagem, com cenas criadas nas pausas
python -m core.engine exports/episodio.mp4 --audio narracao.mp3 --style crayon_book --aspect 9:16 --locale pt-BR --alignment exports/episodio.json

# Áudio externo com transcrição literal fornecida em TXT
python -m core.engine transcricao.txt exports/episodio.mp4 --audio narracao.wav --locale pt-BR

# Nuvem com episódio e áudio externo
./render_colab.sh examples/guerra_dos_emus.json --audio narracao.wav --aspect 9:16
```

Use `--audio` com um JSON para manter suas cenas, ou omita o JSON para buscar pausas e visar cenas de 5–9 s. O modo mantém a duração da faixa até o arredondamento de um quadro e desconsidera as pausas artificiais do roteiro. O fade de 30 ms atua nas pontas da faixa. O Whisper usa CUDA/FP16 quando disponível e CPU/INT8 como fallback. `FRIMERISE_WHISPER_MODEL` seleciona o modelo ou diretório local, com padrão `small`. [Faster Whisper](https://github.com/SYSTRAN/faster-whisper)

Quando há roteiro em JSON ou TXT, o Whisper recebe o texto como prompt e fornece os tempos. As legendas usam a grafia e a pontuação do roteiro, inclusive com `--audio`. Na entrada apenas por áudio, as legendas usam as palavras reconhecidas. O Edge TTS tem um limite por requisição. Para textos maiores, use um provedor capaz de sintetizar o roteiro numa requisição ou envie a faixa pronta com `--audio`.

Cada plugin recebe `SceneTiming.start_sec`, `end_sec`, `duration_sec`, `progress(t)` e `caption_blocks`. Os tempos das palavras e dos blocos são locais à cena no plugin e absolutos no JSON de alinhamento. Em modo contínuo, `audio.path` é compartilhado e `audio.start_sample` e `audio.samples` delimitam o trecho para análise.

### Áudio de estúdio

```bash
python -m core.engine examples/origem_qr_code.json exports/origem_qr_code_audio_studio.mp4 \
  --continuous --tts-engine edge --bgm auto --audio-report exports/relatorio_audio.json

python -m core.engine episodio.json exports/episodio.mp4 --audio narracao.wav \
  --denoise --bgm trilha.mp3 --audio-report exports/relatorio_audio.json

./render_colab.sh examples/origem_qr_code.json --continuous --tts-engine voxcpm2 \
  --voice-ref referencia.wav --bgm auto
```

A voz passa por filtro de graves, equalização, compressão e limiter do Pedalboard, com filtros equivalentes do FFmpeg como fallback. `--bgm auto` gera uma cama instrumental procedural original. A trilha fica em −20 dB nas pausas e o sidechain usa a voz para reduzir seu ganho em até 12 dB adicionais durante a fala. O `librosa` detecta as batidas. Os cartuchos podem usar `SceneTiming.beats_sec` e `core.audio_master.safe_beat` para mover um carimbo ou wipe em até 150 ms, dentro de uma pausa entre palavras. Os cortes de cena e a legenda mantêm o alinhamento original.

`--denoise` habilita o DeepFilterNet em 48 kHz quando houver o executável `deep-filter` no `PATH` ou o backend Python instalado. No Colab, o launcher tenta preparar o executável quando a opção é usada. Se estiver indisponível, a masterização segue com `denoise_applied: false` no relatório. O mix final tem fades de 30 ms e passa pela normalização em duas passagens do FFmpeg. [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet)

### Motion Editorial

O terceiro cartucho usa Chromium headless e GSAP local para compor explicadores com gráficos em SVG e legendas por palavra. Instale o Chromium com `python -m playwright install --only-shell chromium`. No Colab, o runner instala o Chromium para episódios que usam esse cartucho. [Skill e comandos](cartridges/motion_editorial/SKILL.md) · [Exemplo do QR Code](examples/origem_qr_code.json)

Informe o nome registrado do cartucho no campo `plugin` da cena. Os cartuchos e demais plugins são descobertos em pacotes Python sob `plugins/`.

```bash
python -m core.engine episodio.json saida.mp4 --keyframes qa
python -m plugins.qc.ffprobe_audit saida.mp4 --episode episodio.json --frames 81
```

`--assets-root` define a base dos caminhos de mídia e da trilha. O padrão é o diretório de execução. Quando `media_plugin` é `local_pack`, o objeto `media` de cada cena mapeia nomes para arquivos. Em `--frames`, informe a contagem esperada para o MP4 que está auditando.

Cada cena recebe frames suficientes para cobrir a fala medida e `pause_after_sec`. A duração da cena é arredondada para cima até o próximo frame quando necessário. A faixa de voz e a trilha são alinhadas à duração resultante, a 48 kHz. A masterização verifica no AAC decodificado −14 LUFS com tolerância de ±0,3 LU e pico verdadeiro de até −1,0 dBTP.

### Recortes fotográficos e LUTs

Para usar os módulos de foto no Ubuntu, instale `python -m pip install -r requirements.txt -r requirements-matting-cpu.txt`. O CPU usa exclusivamente `birefnet-general-lite` em ONNX. No Colab, o launcher instala as dependências extras ao detectar cenas de recorte e usa o `ZhengPeng7/BiRefNet` em FP16 na L4. Os PNGs RGBA ficam em `.cache/cutouts/`, e os pesos de CPU em `.cache/models/`. O campo `license_commercial_safe` do relatório se refere aos pesos MIT do modelo. Confira separadamente a licença das fotos no arquivo `*_fontes.md`. [Confirmação do autor sobre a licença dos pesos](https://github.com/ZhengPeng7/BiRefNet/issues/316)

A coleção `assets/luts/` inclui `editorial_teal_amber`, `vintage_paper_warm` e `doc_clean_neutral`. Gere as tabelas com `python -m core.color_grade`. O tratamento das fotos é cacheado, e o FFmpeg aplica a LUT do B-roll antes de compor o primeiro plano.

```bash
python -m core.engine examples/teste_colagem_revista.json exports/teste_colagem_revista.mp4 \
  --continuous --bgm auto --audio-report exports/relatorio_passo2.json
```

## Configuração de voz

- O Gemini usa `GEMINI_API_KEY` ou `GOOGLE_API_KEY`. Também aceita, em modo somente leitura, `frimerise.db` com tabela `settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)` e entrada `GEMINI_API_KEY`. `FRIMERISE_DB` permite indicar outro caminho. O campo `speech_style` define a interpretação e `FRIMERISE_GEMINI_TTS_MODEL` seleciona o modelo. [Gemini TTS](https://aistudio.google.com/docs/speech-generation)
- `edge_kokoro_tts` usa Edge TTS com `pt-BR-AntonioNeural` por padrão. Para escolher um modelo Kokoro local, instale `kokoro` e eSpeak NG e use uma voz como `kokoro:pf_dora`. A mesma sintaxe vale em `fallback_voice_id`. [Vozes do Kokoro](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)
- `--tts-engine edge` usa o motor leve. `--tts-engine kokoro --voice pf_dora` seleciona o Kokoro opcional. `--tts-engine voxcpm2` usa o modelo Apache-2.0 em CUDA e aceita `--voice-ref referencia.wav` para condicionar a voz. A saída do VoxCPM2 é de 48 kHz. O Colab instala `requirements-colab.txt` ao selecionar o motor. [VoxCPM2](https://github.com/OpenBMB/VoxCPM)
- Quando o TTS não fornece tempos por palavra, o plugin Whisper os calcula a partir da fala gravada. `FRIMERISE_WHISPER_MODEL` seleciona o modelo ou o caminho local. O padrão é `small`. [Faster Whisper](https://github.com/SYSTRAN/faster-whisper)

Defina `tts_locale: "pt-BR"` para exigir confirmação de localidade do provedor, inclusive no fallback. Nesse modo, o Edge TTS aceita `pt-BR-FranciscaNeural` e `pt-BR-AntonioNeural`. Para áudio pré-gravado, declare `locale` no objeto do manifesto junto à lista `recordings`. O campo `provider: "edge_tts"` também exige que `voice` seja uma das vozes nativas autorizadas.

O mapa em `core/tts_pronunciation.py` adapta termos como `QR Code` e `ARPANET` somente no texto enviado ao sintetizador. O roteiro, os manifestos de voz gravada e a grafia das legendas são preservados.

Se o provedor de voz falhar, `tts_fallback_plugin` aplica o fallback ao episódio inteiro para manter a voz consistente entre cenas. O padrão é `edge_kokoro_tts`. Use `null` para desativá-lo.
