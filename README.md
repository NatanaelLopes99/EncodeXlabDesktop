# Video Compressor Desktop (EPUB)

Aplicativo em Python para reduzir o tamanho de arquivos `.epub.zip`, recomprimindo imagens com Pillow e videos com FFmpeg sem piorar arquivos que ja estao otimizados.

Esta e a versao desktop do projeto, com interface Tkinter e processamento de obras ZIP/EPUB. A primeira versao web esta no repositorio `EncodeXlab`; o desenvolvimento desta aplicacao continua em [EncodeXlabDesktop](https://github.com/NatanaelLopes99/EncodeXlabDesktop).

## Principais recursos
- CLI (`python -m src.main`) com suporte a arquivos isolados, diretorios e multiplos `-i` em lote, alem da GUI (Tkinter).
- Imagens e videos so sao substituidos quando o novo arquivo fica menor que o original.
- Detecta automaticamente NVENC (placas NVIDIA) via FFmpeg e utiliza quando disponivel, com fallback para CPU.
- Quando NVENC esta ativo, tenta aproveitar decodificacao acelerada por hardware antes de re-encodar.
- Copia as trilhas de audio sempre que possivel, evitando recompressao desnecessaria e preservando qualidade.
- Permite definir CRFs alternativos para videos caso o valor principal falhe (sempre menores que o CRF configurado).
- Reconstrucao do EPUB preservando a regra do arquivo `mimetype` sem compressao.
- Saida padrao `nome.compressed.epub.zip`, com opcao de definir o destino final.
- Para cada livro processado, gera um `.log.txt` listando imagens comprimidas e mantidas.
- Preserva entradas originais e saidas existentes; nomes repetidos recebem um numero, como `livro.compressed.epub (2).zip`.
- Valida os caminhos dos arquivos durante a extracao. EPUBs internos com falha de extracao sao mantidos inteiros e registrados como erro.

## Requisitos
- Windows 10 ou 11
- Python 3.10+
- FFmpeg instalado e presente no `PATH` (`ffmpeg` e `ffprobe`)

## Setup rapido (PowerShell)
```powershell
# 1) Criar e ativar o ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) Instalar dependencias
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Uso via interface grafica
```powershell
python -m src.app.gui
```

Na janela:
- Use `Pasta...` ou `Arquivo...` para apontar a pasta com as obras `.zip/.epub.zip` (subpastas sao varridas automaticamente).
- Defina a pasta de saida (opcional). Quando informada, todos os `.compressed.zip` e seus respectivos `.log.txt` sao movidos para la.
- Opcionalmente informe CRFs alternativos (ex: 22,24) — sempre menores que o CRF principal — para videos que falharem com o CRF principal.
- Clique em **Processar obras** e acompanhe o andamento no painel de log. Ao final, um resumo com os caminhos finais e logs e exibido.
- Defina a quantidade de processos paralelos (padrao 1) para processar varias obras.
- O botao **Parar** cancela tarefas que ainda puderem ser canceladas e aguarda as que ja estao em andamento. Os resultados concluidos continuam sendo organizados e registrados nos logs.

## Gerar executavel para Windows
O projeto inclui um script que utiliza o PyInstaller 6 para empacotar a GUI em uma pasta com o executavel Windows (`VideoCompressorDesktop.exe`) e suas dependencias.

```powershell
# Executa o build (cria venv isolado automaticamente)
.\scripts\build_executable.ps1
```

Resultado:
- Executavel em `dist\VideoCompressorDesktop\VideoCompressorDesktop.exe`.
- Nenhuma dependencia Python adicional e necessaria na maquina destino.

Observacoes importantes:
- O build **nao inclui** FFmpeg; a maquina final ainda precisa ter `ffmpeg.exe` e `ffprobe.exe` acessiveis no `PATH`.
- Arquivos de exemplo em `assets\` ficam de fora do pacote final para manter o tamanho reduzido.
- Caso queira forcar dependencia limpa, execute o script com `-RecreateVenv`.
- Para distribuir, copie a pasta `dist\VideoCompressorDesktop` para a maquina destino e execute `VideoCompressorDesktop.exe`.

## Pacote para distribuicao

Depois do build, gere o ZIP para compartilhar ou anexar a uma release no GitHub:
```powershell
.\scripts\package_release.ps1
```

O pacote e criado em `dist\EncodeXlabDesktop-Windows-x64.zip`, com a pasta inteira do aplicativo, a licenca e instrucoes de uso. Extraia todo o ZIP antes de executar; Python nao e necessario na maquina destino, mas FFmpeg/ffprobe devem estar no `PATH`.

Os arquivos de `tmp_samples/` sao exemplos sinteticos. Resultados e logs gerados ficam fora do controle de versao para evitar publicar caminhos locais ou obras processadas.

## Uso via linha de comando
```powershell
python -m src.main -i  "c:\caminho\arquivo.epub.zip"
```

Tambem e possivel apontar pastas ou repetir `-i` para lote:
```powershell
# Processa todos os .zip dentro de assets\books (ignora arquivos .compressed.zip)
python -m src.main -i ".\assets\books"

# Processa dois caminhos e envia a saida para uma pasta especifica
python -m src.main -i ".\assets\books" -i ".\mais_livros" -o ".\saida"
```

Argumentos disponiveis:
- `--input` / `-i`: arquivos ou diretorios `.zip` / `.epub.zip` (use varias vezes para lote).
- `--output` / `-o`: arquivo final (quando houver um unico input) ou diretorio destino para multiplos.
- `--image-quality`: qualidade de JPEG/WEBP (padrao 85).
- `--video-crf`: CRF para videos H.264 (padrao 23, recomendados 18-28).
- `--video-crf-fallback`: CRF(s) alternativos menores que o principal; repita a opcao para testar outros valores em caso de falha.
- `--encoder`: `auto` (padrao), `libx264` (CPU) ou `h264_nvenc` (GPU NVIDIA).
- `--cpu-only`: forca o uso do encoder CPU mesmo que haja suporte NVENC.
- `--workers`: quantidade de processos paralelos para obras diferentes (padrao 1).

Para cada arquivo gerado, um `.log.txt` e salvo no mesmo diretorio com os caminhos das imagens comprimidas/mantidas, o encoder utilizado nos videos e eventuais CRFs alternativos aplicados.

O destino `--output` nao pode ser o proprio arquivo de entrada. Se o nome de saida ja existir, uma nova saida numerada e criada. Os parametros de qualidade e a reducao de tamanho nos logs nao representam uma medicao de perda visual.

## Testes
```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

A suite cobre preservacao de originais, nomes repetidos, falhas de gravacao, caminhos inseguros de ZIP, estrutura EPUB, compressao de imagens e cancelamento paralelo. O teste de video utiliza FFmpeg/ffprobe reais e e ignorado quando eles nao estao instalados.

## Instalacao do FFmpeg
1. Baixe uma distribuicao em https://www.gyan.dev/ffmpeg/builds/.
2. Extraia o pacote e adicione a pasta `bin` ao `PATH` do Windows.
3. Valide a instalacao:
   ```powershell
   ffmpeg -version
   ffprobe -version
   ```

## Estrutura do projeto
```
src/
  app/
    gui.py
  core/
    compression_result.py
    epub_processor.py
    image_compressor.py
    video_compressor.py
  utils/
    fs.py
README.md
requirements.txt
```

## Proximos passos
- [ ] Drag and drop na GUI.
- [ ] Configuracoes persistentes (JSON ou .env).
- [x] Processar varios arquivos em lote.
- [ ] Perfis de compressao predefinidos.
- [x] Processamento paralelo de obras diferentes.

## Licenca
Este projeto utiliza a [licenca MIT](LICENSE).
