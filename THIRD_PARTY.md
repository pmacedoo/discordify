# Componentes de terceiros

O executável inclui componentes de terceiros, sem modificações em seus códigos:

| Componente | Versão | Licença e fontes |
| --- | --- | --- |
| Python | 3.11 (build local: 3.11.0) | [PSF](https://docs.python.org/3/license.html), [fontes](https://www.python.org/downloads/source/) |
| PySide6 / Qt | 6.12.0 | [LGPLv3 / GPLv3 e avisos](https://doc.qt.io/qtforpython-6/licenses.html), [fontes PySide](https://code.qt.io/cgit/pyside/pyside-setup.git/), [fontes Qt](https://download.qt.io/archive/qt/6.12/) |
| PyInstaller | 6.22.3 | [GPL com exceção para o executável gerado e fontes](https://github.com/pyinstaller/pyinstaller) |
| FFmpeg | 7.1 essentials, Gyan.dev | GPLv3 ou posterior, [fontes e builds](https://www.gyan.dev/ffmpeg/builds/), [fontes FFmpeg 7.1](https://ffmpeg.org/releases/ffmpeg-7.1.tar.xz) |

O FFmpeg é obtido pelo pacote [imageio-ffmpeg 0.6.0](https://github.com/imageio/imageio-ffmpeg),
cujo código é BSD-2-Clause. O binário Windows é o build
`7.1-essentials_build-www.gyan.dev`, com `--enable-gpl --enable-version3` e libx264.
Para conferir a configuração completa, execute `src/ffmpeg.exe -version` após o build.
As fontes dos componentes incluídos no build FFmpeg são disponibilizadas pelo provedor acima.

A licença GPL do Discordify não substitui as licenças próprias desses componentes.
