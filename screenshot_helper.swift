import Cocoa
import Carbon
import CoreGraphics

// screenshot_helper — native macOS companion for main.py / server.py
//
// Modes:
//   screenshot_helper <output-dir> [capture-config.json]
//       Daemon: global hotkeys (Ctrl+Shift+S screenshot, Ctrl+Shift+W selection).
//       Also accepts "SNAP" lines on stdin. The capture config is re-read on every
//       screenshot, so the capture area can be changed while recording.
//   screenshot_helper --select-region     Drag-select overlay → prints REGION:{json}
//   screenshot_helper --pick-window       Click-a-window overlay → prints WINDOW:{json}
//   screenshot_helper --list-windows      Prints on-screen windows as a JSON array
//   screenshot_helper --capture <config.json> <out.png>   One-shot capture (preview)
//
// Capture config (JSON):
//   {"mode": "full"}
//   {"mode": "region", "rect": {"x":..,"y":..,"w":..,"h":..}}   global coords, points, top-left origin
//   {"mode": "window", "windowId": 123, "owner": "Microsoft Teams", "title": "..."}

var gScreenshotDir = ""
var gConfigPath: String? = nil

// ── Capture config ───────────────────────────────────────────────────────────

struct CaptureConfig {
    var mode = "full"
    var rect = CGRect.zero
    var windowID: CGWindowID = 0
    var owner = ""
    var title = ""
}

func num(_ v: Any?) -> CGFloat {
    if let n = v as? NSNumber { return CGFloat(n.doubleValue) }
    return 0
}

func loadConfig(_ path: String?) -> CaptureConfig {
    var c = CaptureConfig()
    guard let path = path,
          let data = FileManager.default.contents(atPath: path),
          let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    else { return c }
    c.mode = obj["mode"] as? String ?? "full"
    if let r = obj["rect"] as? [String: Any] {
        c.rect = CGRect(x: num(r["x"]), y: num(r["y"]), width: num(r["w"]), height: num(r["h"]))
    }
    c.windowID = CGWindowID(num(obj["windowId"]))
    c.owner = obj["owner"] as? String ?? ""
    c.title = obj["title"] as? String ?? ""
    return c
}

// ── Windows ──────────────────────────────────────────────────────────────────

func boundsOf(_ w: [String: Any]) -> CGRect {
    guard let d = w[kCGWindowBounds as String] else { return .zero }
    return CGRect(dictionaryRepresentation: d as! CFDictionary) ?? .zero
}

/// On-screen, normal-layer windows of other apps, front to back.
func visibleWindows() -> [[String: Any]] {
    let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let info = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else { return [] }
    let myPID = ProcessInfo.processInfo.processIdentifier
    return info.filter { w in
        let layer = w[kCGWindowLayer as String] as? Int ?? 1
        let pid = w[kCGWindowOwnerPID as String] as? Int32 ?? 0
        let b = boundsOf(w)
        return layer == 0 && pid != myPID && b.width >= 40 && b.height >= 40
    }
}

func windowJSON(_ w: [String: Any]) -> [String: Any] {
    let b = boundsOf(w)
    return [
        "windowId": w[kCGWindowNumber as String] as? Int ?? 0,
        "owner": w[kCGWindowOwnerName as String] as? String ?? "",
        "title": w[kCGWindowName as String] as? String ?? "",
        "rect": ["x": b.origin.x, "y": b.origin.y, "w": b.width, "h": b.height],
    ]
}

/// Find the configured window: by id first, then by owner + title, then by owner.
func resolveWindow(_ c: CaptureConfig) -> CGWindowID? {
    let all = (CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]]) ?? []
    if c.windowID != 0, all.contains(where: { ($0[kCGWindowNumber as String] as? Int) == Int(c.windowID) }) {
        return c.windowID
    }
    let candidates = visibleWindows().filter { ($0[kCGWindowOwnerName as String] as? String) == c.owner }
    if let w = candidates.first(where: { ($0[kCGWindowName as String] as? String) == c.title }) ?? candidates.first,
       let id = w[kCGWindowNumber as String] as? Int {
        return CGWindowID(id)
    }
    return nil
}

/// Returns the image plus the captured rect in global CG coordinates (for the flash).
func captureImage(_ c: CaptureConfig) -> (CGImage, CGRect)? {
    if c.mode == "region" && c.rect.width >= 4 && c.rect.height >= 4 {
        if let img = CGWindowListCreateImage(c.rect, .optionOnScreenOnly, kCGNullWindowID, [.bestResolution]) {
            return (img, c.rect)
        }
    } else if c.mode == "window" {
        if let wid = resolveWindow(c),
           let img = CGWindowListCreateImage(.null, .optionIncludingWindow, wid, [.boundsIgnoreFraming, .bestResolution]),
           img.width > 1 {
            let info = (CGWindowListCopyWindowInfo([.optionIncludingWindow], wid) as? [[String: Any]])?.first
            return (img, info.map(boundsOf) ?? .zero)
        }
        fputs("[screenshot-helper] window '\(c.owner)' not found, capturing full screen\n", stderr)
    }
    guard let img = CGDisplayCreateImage(CGMainDisplayID()) else { return nil }
    return (img, CGDisplayBounds(CGMainDisplayID()))
}

func writePNG(_ image: CGImage, _ path: String) -> Bool {
    let url = URL(fileURLWithPath: path)
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else {
        return false
    }
    CGImageDestinationAddImage(dest, image, nil)
    return CGImageDestinationFinalize(dest)
}

// ── Coordinates ──────────────────────────────────────────────────────────────

/// Height of the primary screen — CG global coords have a top-left origin there.
func primaryHeight() -> CGFloat { NSScreen.screens.first?.frame.height ?? 0 }

func cocoaToCG(_ p: NSPoint) -> CGPoint { CGPoint(x: p.x, y: primaryHeight() - p.y) }

func cgRectToCocoa(_ r: CGRect) -> NSRect {
    NSRect(x: r.origin.x, y: primaryHeight() - r.origin.y - r.height, width: r.width, height: r.height)
}

// ── Capture flash (visual "it was taken" feedback, shown after the capture) ─

var gFlashWindows: [NSWindow] = []

func flash(_ cgRect: CGRect) {
    guard cgRect.width > 0 else { return }
    let frame = cgRectToCocoa(cgRect)
    let win = NSWindow(contentRect: frame, styleMask: .borderless, backing: .buffered, defer: false)
    win.isOpaque = false
    win.backgroundColor = .clear
    win.ignoresMouseEvents = true
    win.level = .screenSaver
    win.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
    let view = NSView(frame: NSRect(origin: .zero, size: frame.size))
    view.wantsLayer = true
    view.layer?.borderColor = NSColor.white.withAlphaComponent(0.95).cgColor
    view.layer?.borderWidth = 4
    view.layer?.backgroundColor = NSColor.white.withAlphaComponent(0.18).cgColor
    win.contentView = view
    win.orderFrontRegardless()
    gFlashWindows.append(win)
    NSAnimationContext.runAnimationGroup({ ctx in
        ctx.duration = 0.45
        win.animator().alphaValue = 0
    }, completionHandler: {
        win.orderOut(nil)
        gFlashWindows.removeAll { $0 === win }
    })
}

// ── Daemon actions ───────────────────────────────────────────────────────────

func takeScreenshot() {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyyMMdd_HHmmss_SSS"
    let filename = "screenshot_\(formatter.string(from: Date())).png"
    let path = (gScreenshotDir as NSString).appendingPathComponent(filename)

    guard let (image, rect) = captureImage(loadConfig(gConfigPath)) else {
        fputs("[screenshot-helper] capture failed (check Screen Recording permission)\n", stderr)
        return
    }
    if writePNG(image, path) {
        print("SCREENSHOT:\(path)")
        fflush(stdout)
        flash(rect)
    } else {
        fputs("[screenshot-helper] failed to write PNG\n", stderr)
    }
}

func getSelectedText() {
    let pasteboard = NSPasteboard.general
    let changeCount = pasteboard.changeCount

    // Simulate Cmd+C to copy selection
    let src = CGEventSource(stateID: .combinedSessionState)
    let cDown = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(kVK_ANSI_C), keyDown: true)
    cDown?.flags = CGEventFlags.maskCommand
    cDown?.post(tap: CGEventTapLocation.cghidEventTap)
    let cUp = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(kVK_ANSI_C), keyDown: false)
    cUp?.flags = CGEventFlags.maskCommand
    cUp?.post(tap: CGEventTapLocation.cghidEventTap)

    // Wait for clipboard to update, then read
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
        if pasteboard.changeCount != changeCount,
           let text = pasteboard.string(forType: .string),
           !text.isEmpty {
            if let data = text.data(using: .utf8) {
                let base64 = data.base64EncodedString()
                print("SELECTION:\(base64)")
                fflush(stdout)
            }
        } else {
            fputs("[screenshot-helper] no selection found\n", stderr)
        }
    }
}

// ── Selection overlay (--select-region / --pick-window) ─────────────────────

enum PickMode { case region, window }

final class OverlayWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

final class OverlayController {
    let mode: PickMode
    var windows: [OverlayWindow] = []
    var dragStart: CGPoint? = nil
    var highlight: CGRect? = nil          // global CG coords
    var hovered: [String: Any]? = nil
    let candidates: [[String: Any]]

    init(mode: PickMode) {
        self.mode = mode
        self.candidates = mode == .window ? visibleWindows() : []
    }

    func start() {
        for screen in NSScreen.screens {
            let win = OverlayWindow(contentRect: screen.frame, styleMask: .borderless,
                                    backing: .buffered, defer: false)
            win.isOpaque = false
            win.backgroundColor = .clear
            win.level = .screenSaver
            win.acceptsMouseMovedEvents = true
            win.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            let view = OverlayView(frame: NSRect(origin: .zero, size: screen.frame.size))
            view.controller = self
            win.contentView = view
            win.makeKeyAndOrderFront(nil)
            win.makeFirstResponder(view)
            windows.append(win)
        }
        NSApp.activate(ignoringOtherApps: true)
        if mode == .window { hover(at: cocoaToCG(NSEvent.mouseLocation)) }
        // Safety net: never leave the screen blocked.
        DispatchQueue.main.asyncAfter(deadline: .now() + 90) { self.cancel() }
    }

    func redraw() { windows.forEach { $0.contentView?.needsDisplay = true } }

    func hover(at p: CGPoint) {
        guard mode == .window else { return }
        hovered = candidates.first { boundsOf($0).contains(p) }
        highlight = hovered.map(boundsOf)
        redraw()
    }

    func down(at p: CGPoint) {
        if mode == .region {
            dragStart = p
            highlight = CGRect(origin: p, size: .zero)
            redraw()
        } else if let w = hovered {
            finish(prefix: "WINDOW", windowJSON(w))
        }
    }

    func drag(to p: CGPoint) {
        guard mode == .region, let s = dragStart else { return }
        highlight = CGRect(x: min(s.x, p.x), y: min(s.y, p.y), width: abs(p.x - s.x), height: abs(p.y - s.y))
        redraw()
    }

    func up(at p: CGPoint) {
        guard mode == .region, let r = highlight else { return }
        if r.width >= 12 && r.height >= 12 {
            let rr = r.integral
            finish(prefix: "REGION", ["rect": ["x": rr.origin.x, "y": rr.origin.y, "w": rr.width, "h": rr.height]])
        } else {
            dragStart = nil
            highlight = nil
            redraw()
        }
    }

    func finish(prefix: String, _ obj: [String: Any]) {
        if let data = try? JSONSerialization.data(withJSONObject: obj),
           let s = String(data: data, encoding: .utf8) {
            print("\(prefix):\(s)")
            fflush(stdout)
        }
        exit(0)
    }

    func cancel() {
        print("CANCEL")
        fflush(stdout)
        exit(1)
    }
}

final class OverlayView: NSView {
    weak var controller: OverlayController?
    var trackingArea: NSTrackingArea?

    override var acceptsFirstResponder: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func updateTrackingAreas() {
        if let t = trackingArea { removeTrackingArea(t) }
        let t = NSTrackingArea(rect: bounds, options: [.mouseMoved, .activeAlways, .inVisibleRect, .cursorUpdate],
                               owner: self, userInfo: nil)
        addTrackingArea(t)
        trackingArea = t
    }

    override func cursorUpdate(with event: NSEvent) {
        (controller?.mode == .region ? NSCursor.crosshair : NSCursor.pointingHand).set()
    }

    func globalPoint(_ event: NSEvent) -> CGPoint {
        guard let win = window else { return .zero }
        return cocoaToCG(win.convertPoint(toScreen: event.locationInWindow))
    }

    override func mouseMoved(with event: NSEvent) { controller?.hover(at: globalPoint(event)) }
    override func mouseDown(with event: NSEvent) { controller?.down(at: globalPoint(event)) }
    override func mouseDragged(with event: NSEvent) { controller?.drag(to: globalPoint(event)) }
    override func mouseUp(with event: NSEvent) { controller?.up(at: globalPoint(event)) }
    override func rightMouseDown(with event: NSEvent) { controller?.cancel() }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == UInt16(kVK_Escape) { controller?.cancel() }
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let win = window, let controller = controller else { return }
        NSColor(calibratedWhite: 0, alpha: 0.38).setFill()
        bounds.fill()

        let accent = NSColor(calibratedRed: 0.49, green: 0.61, blue: 1.0, alpha: 1)
        if let hl = controller.highlight, hl.width > 0, hl.height > 0 {
            let g = cgRectToCocoa(hl)
            let local = NSRect(x: g.origin.x - win.frame.origin.x, y: g.origin.y - win.frame.origin.y,
                               width: g.width, height: g.height)
            NSGraphicsContext.current?.compositingOperation = .clear
            local.fill()
            NSGraphicsContext.current?.compositingOperation = .sourceOver
            accent.withAlphaComponent(0.10).setFill()
            local.fill()
            accent.setStroke()
            let path = NSBezierPath(rect: local.insetBy(dx: -1, dy: -1))
            path.lineWidth = 2
            path.stroke()

            var label = "\(Int(hl.width)) × \(Int(hl.height))"
            if controller.mode == .window, let w = controller.hovered {
                let owner = w[kCGWindowOwnerName as String] as? String ?? ""
                let title = w[kCGWindowName as String] as? String ?? ""
                label = title.isEmpty ? owner : "\(owner) — \(title)"
            }
            drawPill(label, at: NSPoint(x: local.minX, y: local.maxY + 8), accent: accent)
        }

        let hint = controller.mode == .region
            ? "Drag to select the capture area  ·  Esc to cancel"
            : "Click a window to capture it  ·  Esc to cancel"
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 15, weight: .medium),
            .foregroundColor: NSColor.white,
        ]
        let size = (hint as NSString).size(withAttributes: attrs)
        let box = NSRect(x: bounds.midX - size.width / 2 - 18, y: bounds.maxY - 90,
                         width: size.width + 36, height: size.height + 16)
        NSColor(calibratedWhite: 0.08, alpha: 0.85).setFill()
        NSBezierPath(roundedRect: box, xRadius: 10, yRadius: 10).fill()
        (hint as NSString).draw(at: NSPoint(x: box.minX + 18, y: box.minY + 8), withAttributes: attrs)
    }

    func drawPill(_ text: String, at p: NSPoint, accent: NSColor) {
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 12, weight: .medium),
            .foregroundColor: NSColor.white,
        ]
        let size = (text as NSString).size(withAttributes: attrs)
        var origin = p
        if origin.y + size.height + 8 > bounds.maxY - 100 { origin.y = p.y - size.height - 30 }
        let box = NSRect(x: origin.x, y: origin.y, width: size.width + 16, height: size.height + 8)
        accent.setFill()
        NSBezierPath(roundedRect: box, xRadius: 6, yRadius: 6).fill()
        (text as NSString).draw(at: NSPoint(x: box.minX + 8, y: box.minY + 4), withAttributes: attrs)
    }
}

// ── One-shot modes ───────────────────────────────────────────────────────────

let args = CommandLine.arguments
guard args.count > 1 else {
    fputs("Usage: screenshot_helper <output-dir> [capture-config.json]\n", stderr)
    fputs("       screenshot_helper --select-region | --pick-window | --list-windows\n", stderr)
    fputs("       screenshot_helper --capture <config.json> <out.png>\n", stderr)
    exit(1)
}

var gOverlay: OverlayController? = nil

switch args[1] {
case "--list-windows":
    let list = visibleWindows().map(windowJSON)
    if let data = try? JSONSerialization.data(withJSONObject: list),
       let s = String(data: data, encoding: .utf8) {
        print(s)
    }
    exit(0)

case "--capture":
    guard args.count > 3 else { fputs("Usage: --capture <config.json> <out.png>\n", stderr); exit(1) }
    guard let (image, _) = captureImage(loadConfig(args[2])), writePNG(image, args[3]) else {
        fputs("[screenshot-helper] capture failed (check Screen Recording permission)\n", stderr)
        exit(1)
    }
    print("OK:\(args[3])")
    exit(0)

case "--select-region", "--pick-window":
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    gOverlay = OverlayController(mode: args[1] == "--select-region" ? .region : .window)
    gOverlay?.start()
    app.run()
    exit(0)

default:
    break
}

// ── Daemon mode ──────────────────────────────────────────────────────────────

gScreenshotDir = args[1]
if args.count > 2 { gConfigPath = args[2] }

// Carbon event handler — dispatch by hotkey ID
let handler: EventHandlerUPP = { (_, event, _) -> OSStatus in
    var hkID = EventHotKeyID()
    GetEventParameter(
        event!, EventParamName(kEventParamDirectObject),
        EventParamType(typeEventHotKeyID), nil,
        MemoryLayout<EventHotKeyID>.size, nil, &hkID
    )
    switch hkID.id {
    case 1:
        fputs("[screenshot-helper] hotkey 1 (screenshot) fired\n", stderr)
        takeScreenshot()
    case 2:
        fputs("[screenshot-helper] hotkey 2 (selection) fired\n", stderr)
        getSelectedText()
    default: break
    }
    return noErr
}

var eventType = EventTypeSpec(
    eventClass: OSType(kEventClassKeyboard),
    eventKind: UInt32(kEventHotKeyPressed)
)
InstallEventHandler(
    GetApplicationEventTarget(),
    handler,
    1,
    &eventType,
    nil,
    nil
)

// Register Ctrl+Shift+S (kVK_ANSI_S = 1)
var hotKeyRef1: EventHotKeyRef?
let hotKeyID1 = EventHotKeyID(signature: OSType(0x53435253), id: 1)
RegisterEventHotKey(
    UInt32(kVK_ANSI_S),
    UInt32(controlKey | shiftKey),
    hotKeyID1,
    GetApplicationEventTarget(),
    0,
    &hotKeyRef1
)

// Register Ctrl+Shift+W (kVK_ANSI_W = 0x0D)
var hotKeyRef2: EventHotKeyRef?
let hotKeyID2 = EventHotKeyID(signature: OSType(0x53435253), id: 2)
RegisterEventHotKey(
    UInt32(kVK_ANSI_W),
    UInt32(controlKey | shiftKey),
    hotKeyID2,
    GetApplicationEventTarget(),
    0,
    &hotKeyRef2
)

// stdin commands (used by main.py --control-stdin): "SNAP" takes a screenshot.
DispatchQueue.global(qos: .userInitiated).async {
    while let line = readLine() {
        let cmd = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if cmd == "SNAP" {
            DispatchQueue.main.async { takeScreenshot() }
        }
    }
}

fputs("Helper ready (Ctrl+Shift+S: screenshot, Ctrl+Shift+W: selection)\n", stderr)

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
app.run()
