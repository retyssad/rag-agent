"""用户上传文件的文本抽取：支持 .md / .markdown / .txt / .pdf。

只做"取文本"这一件事，不猜测内容：PDF 若无文本层（扫描件/图片型），
明确报错而不是静默入库空内容。
"""
import io

TEXT_TYPES = {'md', 'markdown', 'txt'}
PDF_TYPES = {'pdf'}
ALLOWED_TYPES = TEXT_TYPES | PDF_TYPES
MAX_BYTES = 10 * 1024 * 1024      # 单文件 10 MB
MAX_CHARS = 200_000               # 单文档入库上限，超出截断并如实标记


class ExtractError(ValueError):
    """文件无法用于入库；作为 ValueError 子类，接口层返回 400。"""


def file_type(filename):
    name = (filename or '').strip().lower()
    return name.rsplit('.', 1)[-1] if '.' in name else ''


def extract(filename, raw):
    """返回 (text, meta)。meta 里记录是否截断、原始字符数等事实。"""
    kind = file_type(filename)
    if kind not in ALLOWED_TYPES:
        raise ExtractError(f'不支持的文件类型 .{kind or "未知"}；仅支持 PDF、Markdown（.md）与纯文本（.txt）')
    if not raw:
        raise ExtractError('文件内容为空')
    if len(raw) > MAX_BYTES:
        raise ExtractError(f'文件超过 {MAX_BYTES // 1024 // 1024} MB 上限')
    if kind in PDF_TYPES:
        text = _extract_pdf(raw)
    else:
        text = _decode_text(raw)
    text = _normalize(text)
    if not text:
        if kind in PDF_TYPES:
            raise ExtractError('该 PDF 没有可提取的文本层（可能是扫描件或图片型 PDF），暂不支持')
        raise ExtractError('文件没有可用的文本内容')
    truncated = len(text) > MAX_CHARS
    return text[:MAX_CHARS], {'file_type': kind, 'chars': len(text), 'truncated': truncated}


def _decode_text(raw):
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ExtractError('文本编码无法识别，请另存为 UTF-8 后重试')


def _extract_pdf(raw):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(raw))
        if getattr(reader, 'is_encrypted', False):
            # 空密码可解则继续，否则明确拒绝，不返回残缺内容。
            try:
                reader.decrypt('')
            except Exception as error:
                raise ExtractError('PDF 已加密，无法提取文本') from error
        pages = [(page.extract_text() or '') for page in reader.pages]
    except ExtractError:
        raise
    except (PdfReadError, ValueError, TypeError, OSError) as error:
        raise ExtractError('PDF 解析失败，文件可能已损坏或格式不受支持') from error
    return '\n\n'.join(pages)


def _normalize(text):
    # 统一换行并压缩过多空行；保留原有段落结构。
    lines = [line.rstrip() for line in (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    out, blank = [], 0
    for line in lines:
        if line.strip():
            blank = 0
            out.append(line)
        else:
            blank += 1
            if blank <= 1:
                out.append('')
    return '\n'.join(out).strip()
