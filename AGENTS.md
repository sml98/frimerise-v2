# FrimeRise v2

- Para dirigir um episódio ilustrado, leia [a skill Crayon Book](skills/crayon_book/SKILL.md) e [os presets](skills/crayon_book/presets.json). Reúna tema ou história, formato e duração numa única rodada quando faltarem dados. Escreva em terceira pessoa, com páginas de 3–5 s, e salve em `episodes/<slug>.json`.
- Use `crayon_book` como estilo principal e mantenha `core/` independente dos cartuchos. Valide o episódio e a montagem. Para uma entrega na nuvem, faça commit, dê `git push` para o remoto autorizado e execute `./render_colab.sh episodes/<slug>.json`. Guarde credenciais fora do Git e confirme a auditoria, a entrega no Drive e o desligamento da GPU.
- Publique cada entrega final no Google Drive em `FrimeRise_Exports/<slug>/`, incluindo os MP4s aprovados e os quadros de QC da entrega. Para renders locais, use o remote rclone configurado para o projeto (padrão `gdrive`). Confira os arquivos no Drive após o envio e compartilhe o link da pasta na entrega.
- Para colagem editorial ou histórica com recortes em stop motion, use [a skill Cut-out Paper](cartridges/cutout_paper/SKILL.md) e [o exemplo da Guerra dos Emus](examples/guerra_dos_emus.json).
- Antes de implementar um estilo novo, consulte a implementação mais próxima em `~/referencias/lemo-opuscar` ou `~/referencias/hyperframes`. Leia o código que entrega o efeito, o demo e sua licença antes de escrever a implementação.
- Reaproveite componentes, curvas de câmera, técnicas de composição e efeitos sonoros do código de referência. Registre no projeto o arquivo de origem, a revisão e a licença de cada adaptação.
- Use OpenMoji ou Twemoji para stickers. Os episódios narrados em português devem declarar `tts_locale: "pt-BR"` e usar vozes nativas ou gravações com a mesma localidade declarada.

## Erros Proibidos (Lições Aprendidas)

- Use somente checkpoints com licença comercial MIT ou Apache explicitamente aprovados em `core/cutout_matting.py`. `bria-rmbg` e `RMBG-2.0` são proibidos. Revise os créditos da própria mídia e deduplique palavras e nomes repetidos.
- Em `subject_depth_title`, fundo e recorte compartilham enquadramento, escala, posição e animação em todos os fotogramas. Restrinja fita aos cantos de papéis retangulares e mantenha todo o bounding box das silhuetas livre.
- Anime câmera, entradas, saídas e deslocamentos a 30 fps. Use `easeOutBack` nas entradas e `easeInOutCubic` na câmera. Reserve 12 Hz ao micro-boil de borda ou textura. Mostre conteúdo no primeiro quadro e distribua mudanças de composição a cada 2,5–3 s em cenas de mais de 5 s.
- Termine cenas em `.`, `!` ou `?` e escreva blocos de legenda com pelo menos duas palavras. Valide o MP4 no `core/qc_gate.py` e revise o `qa_grid.png`. Entregue somente com zero janelas mortas, quadros pretos, congelamentos e silêncios detectados, −14 ±0,3 LUFS, pico verdadeiro ≤−1 dBTP e diferença de duração ≤0,15 s.
- Selecione B-roll em planos detectados para evitar chutar timestamps. Escreva `broll_description` descrevendo o que deve aparecer e preserve a narração literal. `media_file` tem prioridade sobre consultas de provedores. Mantenha pesos, índices e vídeos fora do Git. Em uma validação na L4, registre método, scores, intervalos e encerramento da sessão.
