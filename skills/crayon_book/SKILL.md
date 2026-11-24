---
name: crayon-book
description: Dirigir episódios ilustrados do FrimeRise em terceira pessoa, compor páginas Crayon Book em 16:9 ou 9:16 e renderizar o episódio aprovado no Colab L4.
---

# Direção Crayon Book

1. Faça uma única rodada curta reunindo tema ou história, formato (`16:9` ou `9:16`) e duração desejada. Aproveite os dados que já foram dados. Se o pedido estiver completo, siga direto.
2. Leia [presets.json](presets.json) para escolher dimensões, voz e ritmo. Consulte [demo_crayon.json](../../episodes/demo_crayon.json) para a estrutura de uma página. Antes de criar um novo recurso visual, consulte a implementação mais próxima em `~/referencias/lemo-opuscar` ou `~/referencias/hyperframes` e registre a origem, revisão e licença.
3. Conte a história inteira em terceira pessoa. Troque diálogos por ações ou discurso indireto. Cada página conta uma ação ou mudança em uma ou duas frases curtas. Use verbos concretos, pontuação que dê espaço para a voz respirar e deixe a imagem contar seus próprios detalhes.
4. Planeje páginas de 3 a 5 segundos incluindo fala e respiro. Use a duração pedida como alvo de montagem e ajuste o texto ou o tempo de ação quando a fala medida pedir. Preserve o fim das palavras, o primeiro 0,9 s de desenho e o tempo necessário à virada.
5. Salve em `episodes/<slug>.json` com `niche_skill: "crayon_book"` e `plugin: "crayon_book"` em cada cena. Use `tts_locale: "pt-BR"` com uma voz aprovada. Se usar `recorded_voice`, salve as tomadas locais, o manifesto e os tempos por palavra sob `assets/voices/<slug>/`. Para 9:16, mantenha título, legenda e ação essencial na região segura indicada no preset. Os mesmos pontos de cena são reenquadrados pelo cartucho.
6. Valide o JSON com `core.schema.EpisodeSpec.from_json`, meça a duração real pela camada de áudio e revise pelo menos uma página representativa. Faça commit do episódio e dos assets necessários e execute `git push` para o remoto autorizado quando o pedido incluir a entrega em nuvem. Se o remoto estiver ausente, registre essa limitação e só publique em um destino Git escolhido pelo usuário. O render Colab aceita a revisão Git local comprometida.
7. Para renderizar na nuvem, rode `./render_colab.sh episodes/<slug>.json`. O script prepara as credenciais locais fora do Git, pega uma L4, exige NVENC, audita e verifica no Drive o MP4 e três keyframes. Aguarde a confirmação de encerramento do Colab. Entregue o caminho no Drive, duração, formato e resultado da auditoria.

## Áudio contínuo

Use `python -m core.engine episodes/<slug>.json exports/<slug>.mp4 --audio narracao.mp3 --alignment exports/<slug>.json` para manter as cenas escritas e alinhá-las à fala. Para criar cenas pelas pausas, omita o episódio ou forneça um TXT com a fala literal. Use `--continuous --tts edge_kokoro_tts --voice pt-BR-FranciscaNeural` para sintetizar o roteiro inteiro numa tomada. O cartucho recebe os blocos sincronizados e ajusta a virada, as entradas e o movimento à duração medida.
