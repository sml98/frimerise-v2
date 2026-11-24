# FrimeRise v2

- Para dirigir um episódio ilustrado, leia [a skill Crayon Book](skills/crayon_book/SKILL.md) e [os presets](skills/crayon_book/presets.json). Reúna tema ou história, formato e duração numa única rodada quando faltarem dados. Escreva em terceira pessoa, com páginas de 3–5 s, e salve em `episodes/<slug>.json`.
- Use `crayon_book` como estilo principal e mantenha `core/` independente dos cartuchos. Valide o episódio e a montagem. Para uma entrega na nuvem, faça commit, dê `git push` para o remoto autorizado e execute `./render_colab.sh episodes/<slug>.json`. Guarde credenciais fora do Git e confirme a auditoria, a entrega no Drive e o desligamento da GPU.
- Para colagem editorial ou histórica com recortes em stop motion, use [a skill Cut-out Paper](cartridges/cutout_paper/SKILL.md) e [o exemplo da Guerra dos Emus](examples/guerra_dos_emus.json).
- Antes de implementar um estilo novo, consulte a implementação mais próxima em `~/referencias/lemo-opuscar` ou `~/referencias/hyperframes`. Leia o código que entrega o efeito, o demo e sua licença antes de escrever a implementação.
- Reaproveite componentes, curvas de câmera, técnicas de composição e efeitos sonoros do código de referência. Registre no projeto o arquivo de origem, a revisão e a licença de cada adaptação.
- Use OpenMoji ou Twemoji para stickers. Os episódios narrados em português devem declarar `tts_locale: "pt-BR"` e usar vozes nativas ou gravações com a mesma localidade declarada.
