import AppKit
import Darwin

// Headless acceptance and terminal starts retain one PID and normal signals.
let resources = Bundle.main.resourceURL!
let python = resources.appendingPathComponent("runtime/python/bin/python3.12")
let arguments = [python.path, "-I", "-B", resources.appendingPathComponent("bootstrap.py").path]
    + Array(CommandLine.arguments.dropFirst())
if CommandLine.arguments.contains("--no-browser") {
    let cargs = arguments.map { strdup($0) } + [nil]
    cargs.withUnsafeBufferPointer { _ = execv(python.path, $0.baseAddress!) }
    perror("Remember Me Python")
    exit(1)
}

final class Launcher: NSObject, NSApplicationDelegate {
    let child = Process()
    var quitting = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem()
        menu.addItem(item)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Remember Me", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        NSApp.mainMenu = menu
        child.executableURL = python
        child.arguments = Array(arguments.dropFirst()) + ["--owner-pid", String(getpid())]
        child.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        child.terminationHandler = { process in
            DispatchQueue.main.async {
                if self.quitting {
                    NSApp.reply(toApplicationShouldTerminate: true)
                } else {
                    if process.terminationStatus != 0 {
                        let alert = NSAlert()
                        alert.messageText = "Remember Me could not run"
                        alert.informativeText = "Another instance may be running. Check ~/Library/Application Support/remember-me/logs/macos-launcher.log."
                        alert.runModal()
                    }
                    NSApp.terminate(nil)
                }
            }
        }
        do { try child.run() } catch {
            let alert = NSAlert()
            alert.messageText = "Remember Me could not start"
            alert.informativeText = error.localizedDescription
            alert.runModal()
            NSApp.terminate(nil)
        }
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if child.isRunning {
            quitting = true
            child.terminate() // SIGTERM; Runtime drains work and releases both writer locks.
            return .terminateLater
        }
        return .terminateNow
    }
}
let application = NSApplication.shared
let launcher = Launcher()
application.delegate = launcher
application.setActivationPolicy(.regular)
application.run()
