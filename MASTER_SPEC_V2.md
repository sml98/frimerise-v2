# FrimeRise v2 — Especificação Master ("Tudo é Plugin" + Console & Cartuchos)

Documento de arquitetura e roteiro de implementação em 4 fases para agentes de código (**Codex**, **DeepSeek Harness / `dsh`**, **Claude Code** ou **Gemini CLI**).

---

## 1. Visão & Princípios Arquiteturais

1. **Núcleo Minimalista (estilo Pi Agent):** `core/` tem no máximo ~400 linhas e é **100% agnóstico de nicho**. Ele não sabe se o vídeo é comédia estilo Paint, documentário sério ou animação explicativa.
2. **Tudo é Plugin (estilo DeepSeek Harness):** TTS, busca de mídia, estilos visuais, pós-processamento de áudio e QC são plugins independentes descobertos automaticamente por registro (`@register_style`, `@register_tts`, `@register_media`).
3. **Áudio Primeiro (*Audio-First*):** a narração é gerada e medida **antes** da renderização de vídeo. A duração de cada cena nasce travada no áudio real (`duração_fala + margem`), eliminando cortes no meio da frase e dessincronia.
4. **Qualidade Travada (Anti-Bugs):**
   - Fade-in e fade-out obrigatórios de **30ms** em todo clipe de voz (zero estalos/cliques entre cenas).
   - Masterização broadcast **`-14.0 LUFS` / `true peak <= -1.0 dBTP`** com trilha em `-24 dB` e *auto-ducking* (ou corte seco de `0.3s` para silêncio antes de *punchlines*).
   - Quebra automática de linha (`textwrap`) em todo texto na tela (nunca truncar strings com `[:44]`).
   - Ritmo dinâmico (**2–4 segundos** por batida visual — nunca telas paradas por 10+ segundos).
   - Detecção automática de encoder: **NVIDIA `h264_nvenc`** (GPU L4/T4 no Colab) → **Intel `h264_vaapi`** (Ubuntu) → **`libx264`** (CPU).
5. **Nuvem Efêmera:** todo render pesado roda na **GPU L4/T4 do Google Colab** via `google-colab-cli`, envia o MP4 final direto para o **Google Drive (`FrimeRise_Exports/`)** via `rclone` e encerra a VM com `colab stop` automaticamente (0 MB presos no SSD local).

---

## 2. Estrutura de Pastas do Repositório Novo (`frimerise-v2`)

```text
frimerise-v2/
├── MASTER_SPEC_V2.md          ← este documento
├── AGENTS.md                  ← regras curtas para agentes (Codex / dsh / Gemini CLI)
├── requirements.txt           ← dependências enxutas (< 10 pacotes)
├── render_colab.sh            ← sobe GPU L4/T4, envia frimerise.db + rclone.conf, renderiza, sobe MP4 e desliga GPU
├── core/
│   ├── __init__.py
│   ├── schema.py              ← EpisodeSpec, SceneSpec, AudioTrackSpec (dataclasses + validação JSON)
│   ├── registry.py            ← auto-descoberta de plugins em plugins/*
│   ├── encoder.py             ← pipe FFmpeg automático (h264_nvenc / h264_vaapi / libx264)
│   ├── text_utils.py          ← textwrap seguro, fontes, cálculo de bounding box sem overflow
│   └── engine.py              ← orquestrador: 1) TTS+Whisper -> 2) Media -> 3) Render -> 4) Audio Master -> 5) QC
├── plugins/
│   ├── tts/
│   │   ├── gemini_tts.py      ← Gemini Expressive TTS (chave do frimerise.db / env)
│   │   └── edge_kokoro_tts.py ← fallback grátis (Kokoro TTS / edge-tts)
│   ├── media/
│   │   ├── local_pack.py      ← carrega assets locais (bonecos, acessórios, stickers, fundos)
│   │   ├── wikimedia.py       ← Wikimedia Commons (histórico)
│   │   └── pixabay.py         ← Pixabay Fotos + Vídeos MP4 (para vídeo-sobre-vídeo)
│   ├── styles/
│   │   └── comedy_paint.py    ← Cartucho 1: bonecos Paint + boil 12fps + expressões + lip-sync RMS + stickers pop
│   ├── audio/
│   │   └── master_dsp.py      ← fade 30ms, mix de SFX por timestamp, trilha com ducking/silêncio cômico, -14 LUFS
│   └── qc/
│       └── ffprobe_audit.py   ← valida streams, duração, loudness e extrai 3 keyframes (10%, 50%, 90%)
├── assets/
│   ├── paint_pack/            ← bonecos base, 8 expressões, 3 bocas, acessórios (bíblico/histórico/geral)
│   ├── stickers/              ← ícones/stickers PNG/SVG com fundo transparente
│   ├── sfx/                   ← ~15 SFX procedurais/CC0 (pop, whoosh, vine_boom, record_scratch, ding, scribble)
│   └── music/                 ← trilhas leves CC0
├── skills/
│   └── comedy_paint/
│       ├── SKILL.md           ← playbook curto (~1.500 tokens) para o agente gerar roteiros e JSONs
│       └── presets.json       ← paletas, tempos de corte (2–3s) e gatilhos de SFX
└── episodes/
    └── demo_paint.json        ← episódio de teste pronto
```

---

## 3. Contrato JSON Universal (`EpisodeSpec`)

Qualquer nicho (comédia Paint, documentário animado, documentário sério) usa exatamente o mesmo envelope:

```json
{
  "title": "A Arca de Noé em 3 Minutos",
  "niche_skill": "comedy_paint",
  "aspect_ratio": "16:9",
  "width": 1920,
  "height": 1080,
  "fps": 24,
  "tts_plugin": "gemini_tts",
  "voice_id": "Orus",
  "music_track": "assets/music/comedy_pizzicato.mp3",
  "export_shorts_clips": true,
  "scenes": [
    {
      "id": "s01",
      "plugin": "comedy_paint",
      "narration": "Imagina receber uma ordem divina pra construir um cruzeiro de madeira no meio do deserto.",
      "pause_after_sec": 0.35,
      "cut_music_for_punchline": false,
      "background": "desert_paper",
      "camera": "zoom_in_fast",
      "transition_in": "cut",
      "characters": [
        {
          "name": "noe",
          "x": 0.35,
          "y": 0.62,
          "pose": "hands_on_head",
          "expression": "shocked",
          "accessory": "beard_robe",
          "talking": true
        }
      ],
      "stickers": [
        { "kind": "question_mark", "x": 0.68, "y": 0.30, "at_word": "deserto", "sfx": "pop" }
      ],
      "caption_highlight": "CRUZEIRO NO DESERTO"
    }
  ]
}
```

---

## 4. Especificação do Cartucho 1 (`plugins/styles/comedy_paint.py`)

- **Estética:** traços estilo MS Paint / *OverSimplified* / *The Paint Explainer* sobre fundo de papel/caderno colorido com textura leve.
- **Framerate duplo ("on twos"):** desenhos, bonecos e *boil* (micro-tremor `< 1.2 px`) mudam a **12 fps**; câmera, entrada de *stickers* (`pop` com *back-out ease* `0 -> 1.2 -> 1.0` em `0.15s`) e zooms rodam a **24 fps**.
- **Bonecos modulares:** desenhados em camadas (`corpo/pose` → `acessório` → `expressão` → `boca`).
- **Lip-sync leve por RMS:** calcula o volume (RMS) do áudio da cena em janelas de `1/12 s` e alterna entre 3 estados de boca (`closed`, `half`, `open`) sem dependências pesadas.
- **Timing cômico:** se `cut_music_for_punchline: true`, a trilha cai para silêncio por `0.30s`, câmera dá *punch-zoom* (`1.22x`) no rosto chocado e dispara o SFX (`vine_boom` ou `record_scratch`).
- **Legendas:** fonte grossa legível na base (com quebra de linha automática, contorno escuro limpo e destaque na palavra de `caption_highlight`).
- **Auto-Shorts (9:16):** se `export_shorts_clips: true`, re-renderiza automaticamente os 2 melhores trechos (30–50s) em `1080x1920` vertical.

---

## 5. Prompts Prontos para o Codex / DeepSeek Harness (`dsh`)

**IMPORTANTE:** Execute **1 fase por vez**. Só avance para a próxima fase quando o teste automático da fase atual passar 100%.

### Prompt — Fase 1 (Núcleo Universal + Encoder + Áudio-First)
> Leia `MASTER_SPEC_V2.md`. Implemente a **Fase 1**: crie `core/schema.py`, `core/registry.py`, `core/text_utils.py`, `core/encoder.py` (com detecção automática `h264_nvenc` -> `h264_vaapi` -> `libx264`), `plugins/audio/master_dsp.py` (com fade de 30ms em cada fala para zero estalo, mix de SFX por timestamp, auto-ducking, silêncio cômico e masterização `-14 LUFS / <= -1.0 dBTP`), `plugins/tts/edge_kokoro_tts.py`, `plugins/tts/gemini_tts.py`, `plugins/qc/ffprobe_audit.py` e o orquestrador `core/engine.py`. Crie um teste automatizado `tests/test_core.py` que valide o carregamento de plugins, a duração exata *Audio-First* e o pipe FFmpeg. Rode `pytest` e só encerre quando tudo passar.

### Prompt — Fase 2 (Cartucho 1: `comedy_paint` + Pack de Assets + SFX)
> Leia `MASTER_SPEC_V2.md`. Implemente a **Fase 2**: crie `plugins/styles/comedy_paint.py` seguindo a seção 4 (bonecos modulares estilo Paint com 5 poses, 8 expressões, acessórios de época, lip-sync por RMS de áudio, *boil* a 12 fps, *stickers* entrando com animação *pop*, legendas com `textwrap` seguro, suporte 16:9 e 9:16). Crie também `scripts/bootstrap_paint_pack.py` para gerar o pack inicial de SFX procedurais limpos (`pop`, `whoosh`, `vine_boom`, `record_scratch`, `ding`, `scribble`) em `assets/sfx/` e crie `episodes/demo_paint.json` (30 segundos, 8 cenas com ritmo de 3–4s). Renderize o demo completo e rode `plugins/qc/ffprobe_audit.py` confirmando duração exata, áudio `-14 LUFS`, 0 estalos e extraindo 3 keyframes PNG para inspeção.

### Prompt — Fase 3 (Skill do Diretor + Infraestrutura Colab GPU + `rclone`)
> Leia `MASTER_SPEC_V2.md`. Implemente a **Fase 3**: crie `skills/comedy_paint/SKILL.md`, `skills/comedy_paint/presets.json`, `AGENTS.md` (ensinando o agente a fazer uma entrevista rápida de 1 rodada, gerar o JSON do episódio com ritmo de 2–4s por cena, dar `git push` e disparar na nuvem) e `render_colab.sh` (usando `colab new -s frime --gpu L4`, `trap 'colab stop -s frime' EXIT`, upload automático do `frimerise.db` e `~/.config/rclone/rclone.conf`, render NVENC na GPU e upload final do MP4 + 3 keyframes para `FrimeRise_Exports/` no Google Drive via `rclone`). Teste `./render_colab.sh episodes/demo_paint.json` ponta a ponta.

### Prompt — Fase 4 (Expansão Multi-Nicho — Futuro)
> Leia `MASTER_SPEC_V2.md`. Sem alterar nenhum arquivo em `core/`, adicione o segundo cartucho `plugins/styles/doc_animated.py` (com suporte opcional a clipes curtos em HTML/GSAP estilo HyperFrames) + `plugins/media/wikimedia.py` e `plugins/media/pixabay.py` + `skills/doc_animated/SKILL.md`, garantindo que `core/registry.py` descubra o novo cartucho automaticamente.
