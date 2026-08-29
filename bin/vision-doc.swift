// vision-doc.swift — структурный OCR через Apple Vision (macOS 26+).
//
// Зачем. Прежний bin/vision-ocr.swift работает на VNRecognizeTextRequest — это
// распознавание СТРОК. Таблица на выходе рассыпается: заголовки идут отдельными
// строками, потом номера колонок, связь ячейки со строкой теряется. На стр. 82
// одного экспертного заключения, где по таблице считались остатки счетов,
// выход выглядит как «Эмитент / Номер гос. рег- / ии, ISIN / Тип ЦБ / … / 10 11 12».
// Таблица там — доказательство, а не оформление.
//
// macOS 26 принес RecognizeDocumentsRequest: он отдает DocumentObservation со
// структурой — параграфы, списки и ТАБЛИЦЫ с сеткой ячеек (rows: [[Cell]],
// у ячейки rowRange/columnRange, то есть объединенные ячейки тоже видны).
// Это снимает единственную причину, по которой в проект тащили тяжелую внешнюю
// модель: структура таблиц теперь есть нативно, локально, за $0 и на скорости
// системного движка.
//
// Использование:
//   vision-doc IMAGE            → Markdown: параграфы + таблицы GFM
//   vision-doc IMAGE --json     → JSON: {tables:[{rows:[[cell]]}], paragraphs:[...]}
//   vision-doc IMAGE --text     → только текст (совместимо со старым vision-ocr)
//
// Языки: ru-RU, en-US. Коррекция включена.

import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count > 1 else {
    FileHandle.standardError.write("usage: vision-doc IMAGE [--json|--text]\n".data(using: .utf8)!)
    exit(2)
}
let path = args[1]
let mode = args.count > 2 ? args[2] : "--md"

guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("load fail: \(path)\n".data(using: .utf8)!)
    exit(1)
}

/// Текст ячейки: у Cell есть .content (Container), берем его параграфы.
func cellText(_ cell: DocumentObservation.Container.Table.Cell) -> String {
    cell.content.paragraphs
        .map { $0.transcript.replacingOccurrences(of: "\n", with: " ") }
        .joined(separator: " ")
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

/// GFM-таблица. Вертикальная черта внутри ячейки экранируется, иначе ломается разметка.
func tableToMarkdown(_ t: DocumentObservation.Container.Table) -> String {
    let rows = t.rows
    guard !rows.isEmpty else { return "" }
    let width = rows.map { $0.count }.max() ?? 0
    guard width > 0 else { return "" }

    func line(_ cells: [DocumentObservation.Container.Table.Cell]) -> String {
        var out = cells.map { cellText($0).replacingOccurrences(of: "|", with: "\\|") }
        while out.count < width { out.append("") }
        return "| " + out.joined(separator: " | ") + " |"
    }

    var md = [line(rows[0])]
    md.append("|" + String(repeating: " --- |", count: width))
    for r in rows.dropFirst() { md.append(line(r)) }
    return md.joined(separator: "\n")
}

func jsonEscape(_ s: String) -> String {
    var o = ""
    for c in s.unicodeScalars {
        switch c {
        case "\"": o += "\\\""
        case "\\": o += "\\\\"
        case "\n": o += "\\n"
        case "\t": o += "\\t"
        case "\r": o += "\\r"
        default:
            if c.value < 0x20 { o += String(format: "\\u%04x", c.value) } else { o.unicodeScalars.append(c) }
        }
    }
    return o
}

let sem = DispatchSemaphore(value: 0)
var exitCode: Int32 = 0

Task {
    defer { sem.signal() }
    var req = RecognizeDocumentsRequest()
    req.textRecognitionOptions.recognitionLanguages = [
        Locale.Language(identifier: "ru-RU"),
        Locale.Language(identifier: "en-US"),
    ]
    req.textRecognitionOptions.useLanguageCorrection = true

    do {
        let results = try await req.perform(on: cg)
        guard let doc = results.first?.document else {
            FileHandle.standardError.write("no document observation\n".data(using: .utf8)!)
            exitCode = 3
            return
        }

        let paragraphs = doc.paragraphs.map { $0.transcript }
        let tables = doc.tables

        switch mode {
        case "--text":
            // Совместимость со старым vision-ocr: плоский текст, но таблицы
            // выводятся построчно ячейками — уже связнее, чем строки экрана.
            for p in paragraphs { print(p) }
            for t in tables {
                for row in t.rows { print(row.map(cellText).joined(separator: "\t")) }
            }

        case "--json":
            var parts: [String] = []
            parts.append("\"paragraphs\":[" + paragraphs.map { "\"\(jsonEscape($0))\"" }.joined(separator: ",") + "]")
            let tj = tables.map { t -> String in
                let rows = t.rows.map { row in
                    "[" + row.map { "\"\(jsonEscape(cellText($0)))\"" }.joined(separator: ",") + "]"
                }.joined(separator: ",")
                return "{\"rows\":[\(rows)]}"
            }.joined(separator: ",")
            parts.append("\"tables\":[\(tj)]")
            parts.append("\"table_count\":\(tables.count)")
            print("{" + parts.joined(separator: ",") + "}")

        default:
            for p in paragraphs where !p.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                print(p)
            }
            for (i, t) in tables.enumerated() {
                let md = tableToMarkdown(t)
                if !md.isEmpty {
                    print("\n<!-- таблица \(i + 1): \(t.rows.count) строк -->")
                    print(md)
                }
            }
        }
    } catch {
        FileHandle.standardError.write("vision error: \(error)\n".data(using: .utf8)!)
        exitCode = 4
    }
}

sem.wait()
exit(exitCode)
