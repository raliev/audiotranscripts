import Cocoa
import Carbon
import CoreGraphics

var gScreenshotDir = ""

func takeScreenshot() {
    let formatter = DateFormatter()
    formatter.dateFormat = "yyyyMMdd_HHmmss_SSS"
    let filename = "screenshot_\(formatter.string(from: Date())).png"
    let path = (gScreenshotDir as NSString).appendingPathComponent(filename)
    let url = URL(fileURLWithPath: path)

    guard let image = CGDisplayCreateImage(CGMainDisplayID()) else {
        fputs("[screenshot-helper] CGDisplayCreateImage failed (check Screen Recording permission)\n", stderr)
        return
    }

    guard let dest = CGImageDestinationCreateWithURL(
        url as CFURL, "public.png" as CFString, 1, nil
    ) else {
        fputs("[screenshot-helper] failed to create image destination\n", stderr)
        return
    }

    CGImageDestinationAddImage(dest, image, nil)
    if CGImageDestinationFinalize(dest) {
        print("SCREENSHOT:\(path)")
        fflush(stdout)
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

// ── Main ─────────────────────────────────────────────────────────────────────

guard CommandLine.arguments.count > 1 else {
    fputs("Usage: screenshot-helper <output-dir>\n", stderr)
    exit(1)
}
gScreenshotDir = CommandLine.arguments[1]

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

fputs("Helper ready (Ctrl+Shift+S: screenshot, Ctrl+Shift+W: selection)\n", stderr)

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
app.run()
