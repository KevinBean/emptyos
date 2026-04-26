# PDF Reader Skill

Extract and read PDF files in chunks to avoid context overflow.

## Setup (One-time)

```bash
pip install pymupdf
```

## Usage

### 1. Get PDF Info

```bash
python pdf_tool.py info "path/to/file.pdf"
```

Returns: total pages, title, author, file size.

### 2. Extract Text (Page Range)

```bash
python pdf_tool.py extract "path/to/file.pdf" --start 1 --end 10
```

- `--start`: Starting page (1-indexed, default: 1)
- `--end`: Ending page (inclusive, default: 10)
- Recommended: Read 10-20 pages at a time

### 3. Extract Table of Contents

```bash
python pdf_tool.py toc "path/to/file.pdf"
```

### 4. Search Text

```bash
python pdf_tool.py search "path/to/file.pdf" "keyword"
```

Returns pages containing the keyword.

---

## Workflow for Reading a Book

1. **Get info first** to know total pages:
   ```bash
   python pdf_tool.py info "book.pdf"
   ```

2. **Extract TOC** to understand structure:
   ```bash
   python pdf_tool.py toc "book.pdf"
   ```

3. **Read in chunks** (10-20 pages per request):
   ```bash
   python pdf_tool.py extract "book.pdf" --start 1 --end 15
   python pdf_tool.py extract "book.pdf" --start 16 --end 30
   # ... continue as needed
   ```

4. **Search for specific topics**:
   ```bash
   python pdf_tool.py search "book.pdf" "emotion"
   ```

---

## Common PDF Locations

| Type | Path |
|------|------|
| Books | `99_Attachments/books/` |
| Papers | `99_Attachments/papers/` |
| Downloads | `~/Downloads/` |

---

## Trigger Phrases

| User Says | Action |
|-----------|--------|
| "读这个 PDF" / "read this PDF" | Get info → TOC → Extract chunks |
| "PDF 有多少页" / "how many pages" | Run `info` command |
| "提取第 X-Y 页" / "extract pages X-Y" | Run `extract --start X --end Y` |
| "搜索 PDF 中的 X" / "search PDF for X" | Run `search` command |

---

## Notes

- Text extraction quality depends on PDF type (scanned vs. native text)
- For scanned PDFs, consider OCR tools (not included in this skill)
- Large PDFs should always be read in chunks to preserve context window
