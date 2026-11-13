// อ่านข้อความบนภาพหน้าจอ: พิมพ์ "x,y|ข้อความ" (x,y = จุดกลาง ในพิกัดจอ 1280x800)
import Vision; import AppKit
let p = CommandLine.arguments[1]
let W = Double(CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "1280")!
let H = Double(CommandLine.arguments.count > 3 ? CommandLine.arguments[3] : "800")!
guard let img = NSImage(contentsOfFile: p), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { exit(1) }
let req = VNRecognizeTextRequest(); req.recognitionLevel = .accurate
req.recognitionLanguages = ["th-TH", "en-US"]; req.usesLanguageCorrection = false
try VNImageRequestHandler(cgImage: cg).perform([req])
for o in req.results ?? [] { guard let t = o.topCandidates(1).first else { continue }
  let b = o.boundingBox; let x = Int((b.midX) * W); let y = Int((1 - b.midY) * H)
  print("\(x),\(y)|\(t.string)") }
