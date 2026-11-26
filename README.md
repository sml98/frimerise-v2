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

### Produção em um comando

```bash
python -m frimerise produce --topic "A história do correio ferroviário" \
  --cartridge cutout_paper --duration 120 --aspect 9:16 --bgm auto

python -m frimerise produce --audio narracao.wav \
  --cartridge motion_editorial --aspect 16:9 --bgm auto

python -m frimerise produce --audio narracao.wav \
  --cartridge cutout_paper --aspect 9:16 --plan-only --output-dir exports/revisao

# Após editar o plano e adicionar um media_file local, reaproveite a locução.
python -m frimerise produce --episode exports/revisao/episodio_gerado.json \
  --audio narracao.wav --bgm auto --output-dir exports/revisao
```

Os três cartuchos aceitam `produce` a 30 fps. O diretor procura a credencial na variável `DEEPSEEK_API_KEY` e depois na entrada `settings.deepseek_api_key` de cada banco, na ordem `FRIMERISE_DB`, `./frimerise.db` e `~/frimerise-cinema-studio/data/frimerise.db`. Após um HTTP 401, tenta a próxima credencial distinta. Se todas falharem, `--topic` encerra com erro antes de gravar o episódio ou renderizar. O diretor envia só um trecho do guia do cartucho escolhido e tenta reparar o JSON uma vez se o plano falhar na validação. [Modelos DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/)

Por tema, o diretor planeja a locução em terceira pessoa e o motor sintetiza uma tomada contínua, ajustando levemente o ritmo para a duração pedida. Com `--audio`, o Whisper segmenta a fala antes de o diretor escolher a composição visual. Inclua `--alignment <arquivo.json>` no render para exportar os tempos. Para retomar um roteiro editado por tema, informe `--episode` e a duração desejada.

Por padrão, as consultas de mídia de `produce` aceitam arquivos de domínio público ou CC0. `--allow-licensed-media` habilita também as fontes com licenças comerciais ou de atribuição já suportadas. Use `media_file` no plano editado com mídia própria ou autorizada, com caminho relativo a `--assets-root` (o diretório atual por padrão).

### Produção editorial de estúdio

Use `--production-profile studio_sota` em novos planos para selecionar Chatterbox PT-BR na L4, com fallback Edge PT-BR. O perfil mantém a renderização a 30 fps e exige parecer editorial no QC. Configure `FRIMERISE_VLM_BACKEND=local_l4` no Colab ou `FRIMERISE_VLM_BACKEND=api` com o endpoint e a credencial correspondente no ambiente. Use `--skip-vlm-qc` nos testes offline.

No Colab, instale `requirements-studio-colab.txt` sobre a base PyTorch CUDA. Use ambientes isolados para Chatterbox e ACE-Step, com `FRIMERISE_CHATTERBOX_PYTHON` e `FRIMERISE_ACE_STEP_PYTHON` apontando para os respectivos interpretadores. A distribuição oficial ACE-Step 1.5 usa o encoder Qwen3-Embedding-0.6B Apache-2.0.

```bash
FRIMERISE_VLM_BACKEND=local_l4 python -m frimerise produce \
  --episode examples/vampiro_1725_v4_sota.json --duration 45 \
  --assets-root /caminho/para/acervo --bgm adaptive --output-dir exports/vampiro_1725_v4_sota
```

O cartucho Cut-out Paper usa `photo_25d_parallax` com profundidade DA3 Apache-2.0 e inpainting MI-GAN MIT. O B-roll pode ser ranqueado contra a fala em português pelo EmbeddingGemma 2. O relatório guarda os modelos efetivamente executados, o parecer por cena, o áudio e a montagem.

Os pacotes de validação no Google Drive incluem os MP4s e os arquivos locais usados pelos roteiros. Baixe a pasta `media/` do episódio e aponte `--assets-root` para o diretório que contém essa pasta. Os exemplos v2 e v3 usam as mídias locais do pacote v3. [Pacote v3](https://drive.google.com/drive/folders/14wqKFDHde_IDeGH5YRBdPts-uzpVkGeF) · [Pacote v4 SOTA](https://drive.google.com/drive/folders/1CqwLsKjlqUeoAE4WVty-3mS9M_3LcRdH)

`dynamic_edit: true` habilita a auditoria editorial e as pausas escritas em `pause_after_sec` na locução contínua. As pausas são inseridas entre palavras alinhadas e entram na duração alvo. `pronunciation_overrides` troca a grafia só na entrada do TTS. A legenda mantém o roteiro original.

`transition` seleciona `cut_on_beat`, `zoom_punch`, `whip_pan`, `paper_rip` ou `flash_cut`. `music_mood` escolhe `tense_low`, `pulse_build`, `drop_silence` ou `resolve`. No modo dinâmico, os relatórios registram os efeitos com tempos por cena, a duração média, a colisão medida no Chromium e a diversidade. [Modos Cut-out Paper](cartridges/cutout_paper/SKILL.md#montagem-dinâmica)

`--plan-only` salva `episodio_gerado.json` e mostra a contagem de tokens no terminal. O render grava `episodio.mp4`, `episodio_fontes.md`, `qa_grid.png` e `relatorio_director.json` em `exports/<slug>/` ou em `--output-dir`. O relatório identifica o modo de entrada e o provedor. Para um plano novo, exige consumo positivo e soma os tokens que a API informou nas chamadas de planejamento e de reparo. Para um roteiro lido com `--episode`, a contagem é zero.

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

A voz passa por um filtro de graves, equalização, compressão e limiter. O DSP de estúdio usa SciPy e DASP com medição de loudness pelo pyloudnorm. `--bgm auto` gera uma cama instrumental procedural original. `--bgm adaptive` ativa a geração instrumental no perfil de estúdio. A trilha fica em −20 dB nas pausas e o sidechain usa a voz para reduzir seu ganho em até 12 dB adicionais durante a fala. O `librosa` detecta as batidas. Os cartuchos podem usar `SceneTiming.beats_sec` e `core.audio_master.safe_beat` para mover um carimbo ou wipe em até 150 ms, dentro de uma pausa entre palavras.

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

#### B-roll semântico e mídia local

`broll_description` descreve o plano desejado. O PySceneDetect separa o filme em planos e indexa até 24 quadros representativos em `.cache/broll_index/`. O seletor descarta planos abaixo de 2 s, pretos ou estáticos. No Colab, o SigLIP 2 cruza a descrição e a fala com os quadros. Na instalação local, brilho, nitidez e movimento classificam os planos. `--audio-report` registra o intervalo selecionado, o método e o score. A seleção usa uma análise a 10 fps e o trecho de saída mantém 30 fps. [PySceneDetect](https://www.scenedetect.com/download/) · [OpenCLIP e pesos SigLIP 2 Apache-2.0](https://huggingface.co/timm/ViT-B-16-SigLIP2-256/tree/fb14461786ff5cd7b18210817522546f28c7143a)

Para usar `--semantic-cpu`, instale primeiro PyTorch e torchvision para CPU e depois `open-clip-torch>=3.2,<4` e `transformers>=4.57,<5` no mesmo ambiente. O seletor usa um frame por lote no CPU e recorre à avaliação visual se não houver memória livre. Os downloads de acervo aproveitam a cópia mais leve encontrada, até 256 MB e 40 minutos por filme.

`media_file` aceita PNG, JPG ou MP4 sem acionar a busca remota para aquele asset. Os caminhos são absolutos ou relativos ao `--assets-root`. O launcher Colab transfere os arquivos referenciados junto ao job, inclusive arquivos locais fora do Git. O render registra a origem própria em `<slug>_fontes.md`.

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
