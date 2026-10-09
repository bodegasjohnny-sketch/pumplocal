// PumpLocal on-device OCR using Apple's Vision framework (built into macOS, fully offline).
//
// Usage:  pumplocal-ocr /path/to/image.jpg
// Prints JSON to stdout:
//   {"width": W, "height": H, "lines": [{"text": "...", "confidence": 0.98,
//     "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05}, ...]}
// Boxes are normalized 0..1 with the origin at the TOP-LEFT (Vision uses bottom-left; we flip y).
//
// Built by app on first use:  swiftc -O ocr/ocr.swift -o ocr/pumplocal-ocr
import Foundation
import ImageIO
import Vision

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("usage: pumplocal-ocr <image-path>") }

let url = URL(fileURLWithPath: args[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fail("cannot open image: \(args[1])")
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false  // meter digits are not words; don't "correct" them

let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
do {
    try handler.perform([request])
} catch {
    fail("vision error: \(error)")
}

var lines: [[String: Any]] = []
for case let observation as VNRecognizedTextObservation in (request.results ?? []) {
    guard let best = observation.topCandidates(1).first else { continue }
    let box = observation.boundingBox
    lines.append([
        "text": best.string,
        "confidence": Double(best.confidence),
        "x": Double(box.minX),
        "y": Double(1.0 - box.maxY),
        "w": Double(box.width),
        "h": Double(box.height),
    ])
}

let output: [String: Any] = ["width": image.width, "height": image.height, "lines": lines]
guard let data = try? JSONSerialization.data(withJSONObject: output, options: []) else {
    fail("could not encode JSON")
}
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write("\n".data(using: .utf8)!)
