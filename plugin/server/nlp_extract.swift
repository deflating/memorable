#!/usr/bin/env swift

import Foundation
import NaturalLanguage

// Read transcript from stdin
var input = ""
while let line = readLine(strippingNewline: false) {
    input += line
}

guard !input.isEmpty else {
    print("{}")
    exit(0)
}

// --- Named Entity Extraction ---
var entities: [String: Set<String>] = [
    "people": [],
    "organizations": [],
    "places": [],
]

let entityTagger = NLTagger(tagSchemes: [.nameType])
entityTagger.string = input
entityTagger.enumerateTags(
    in: input.startIndex..<input.endIndex,
    unit: .word,
    scheme: .nameType,
    options: [.omitWhitespace, .omitPunctuation, .omitOther, .joinNames]
) { tag, range in
    if let tag = tag {
        let value = String(input[range]).trimmingCharacters(in: .whitespacesAndNewlines)
        // Skip generic role labels
        let skip = ["User", "Claude", "Assistant", "Human"]
        guard !skip.contains(value), value.count > 1 else { return true }

        switch tag {
        case .personalName:
            entities["people"]!.insert(value)
        case .organizationName:
            entities["organizations"]!.insert(value)
        case .placeName:
            entities["places"]!.insert(value)
        default:
            break
        }
    }
    return true
}

// --- Sentence-level Sentiment ---
let sentimentTagger = NLTagger(tagSchemes: [.sentimentScore])
sentimentTagger.string = input

var sentimentScores: [Double] = []
var negativeSentences: [(Double, String)] = []
var positiveSentences: [(Double, String)] = []

sentimentTagger.enumerateTags(
    in: input.startIndex..<input.endIndex,
    unit: .sentence,
    scheme: .sentimentScore
) { tag, range in
    if let tag = tag, let score = Double(tag.rawValue) {
        sentimentScores.append(score)
        let sentence = String(input[range]).trimmingCharacters(in: .whitespacesAndNewlines)
        guard sentence.count > 20 else { return true }  // skip tiny fragments

        if score <= -0.5 {
            negativeSentences.append((score, sentence))
        } else if score >= 0.5 {
            positiveSentences.append((score, sentence))
        }
    }
    return true
}

let avgSentiment = sentimentScores.isEmpty ? 0.0 : sentimentScores.reduce(0, +) / Double(sentimentScores.count)

// Pick top 3 most extreme sentences each way
let topNegative = negativeSentences.sorted { $0.0 < $1.0 }.prefix(3)
let topPositive = positiveSentences.sorted { $0.0 > $1.0 }.prefix(3)

// --- Dominant Language ---
let recognizer = NLLanguageRecognizer()
recognizer.processString(input)
let language = recognizer.dominantLanguage?.rawValue ?? "unknown"

// --- Word/Sentence counts ---
let tokenizer = NLTokenizer(unit: .sentence)
tokenizer.string = input
var sentenceCount = 0
tokenizer.enumerateTokens(in: input.startIndex..<input.endIndex) { _, _ in
    sentenceCount += 1
    return true
}

// --- Build JSON output ---
func jsonArray(_ items: [String]) -> String {
    let escaped = items.map { "\"\($0.replacingOccurrences(of: "\"", with: "\\\""))\"" }
    return "[\(escaped.joined(separator: ", "))]"
}

func jsonSentences(_ items: [(Double, String)]) -> String {
    let entries = items.map { score, text in
        let clean = text.replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: " ")
        let truncated = clean.count > 200 ? String(clean.prefix(200)) + "..." : clean
        return "{\"score\": \(String(format: "%.2f", score)), \"text\": \"\(truncated)\"}"
    }
    return "[\(entries.joined(separator: ", "))]"
}

// Sentiment label
let sentimentLabel: String
switch avgSentiment {
case ..<(-0.5): sentimentLabel = "very_negative"
case ..<(-0.2): sentimentLabel = "negative"
case ..<0.2: sentimentLabel = "neutral"
case ..<0.5: sentimentLabel = "positive"
default: sentimentLabel = "very_positive"
}

let json = """
{
  "entities": {
    "people": \(jsonArray(Array(entities["people"]!).sorted())),
    "organizations": \(jsonArray(Array(entities["organizations"]!).sorted())),
    "places": \(jsonArray(Array(entities["places"]!).sorted()))
  },
  "sentiment": {
    "average": \(String(format: "%.2f", avgSentiment)),
    "label": "\(sentimentLabel)",
    "sentence_count": \(sentenceCount),
    "most_negative": \(jsonSentences(Array(topNegative))),
    "most_positive": \(jsonSentences(Array(topPositive)))
  },
  "language": "\(language)"
}
"""

print(json)
