import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// A CyberPower UPS in the Omarchy bar.
//
// The real work lives in bin/cyberpower-ups, a self-contained Python reader
// that talks to the UPS over USB HID (or falls back to pwrstat / NUT) and
// prints one JSON object. This widget only polls that script, turns the JSON
// into a glyph and a popup, and shells out again for the self-test and mute
// actions. Keeping the protocol knowledge in the script means the widget does
// no guessing about report layouts, and the reader is testable on its own.
BarWidget {
  id: root
  moduleName: "io.github.kamil.cyberpower-ups"

  // Last parsed status document, or null before the first successful poll.
  property var status: null
  property string errorText: ""
  property bool popupOpen: false
  property string actionMessage: ""
  // Whether the last poll ever ran, and whether it ever ended. A poll that
  // starts and never ends means the reader could not be started at all.
  property bool pollStarted: false
  property bool pollFinished: false

  // The shutdown rule's state. The clock starts when the UPS is first seen
  // on battery; the countdown runs while the rule is waiting to be obeyed.
  property double outageStarted: 0
  property double shutdownTriggered: 0
  property int shutdownCountdown: -1
  property string shutdownReason: ""
  property bool shutdownCancelled: false

  readonly property string reader: decodeURIComponent(
    String(Qt.resolvedUrl("bin/cyberpower-ups")).replace("file://", ""))
  readonly property string tui: decodeURIComponent(
    String(Qt.resolvedUrl("bin/cyberpower-ups-tui")).replace("file://", ""))

  // The reader speaks in lowercase tokens; the settings UI shows prose.
  // The setting is called "backend", not "source": a bar entry that carries
  // a setting called "source" is read as a custom QML module by the shell,
  // and the widget then renders as nothing at all.
  readonly property string source: {
    var choice = root.setting("backend", "Auto")
    if (choice === "USB direct") return "hidraw"
    if (choice === "pwrstat") return "pwrstat"
    if (choice === "NUT") return "nut"
    if (choice === "Simulate") return "simulate"
    return "auto"
  }

  readonly property int intervalSec: Math.max(5, root.numberSetting("intervalSec", 15))
  readonly property bool showPercent: root.boolSetting("showPercent", true)
  readonly property string upsName: String(root.setting("upsName", "") || "")
  readonly property int warnBattery: root.numberSetting("warnBattery", 30)
  readonly property int criticalBattery: root.numberSetting("criticalBattery", 15)
  readonly property string iconPreset: String(root.setting("iconPreset", "Auto") || "Auto")
  readonly property bool showTestControls: root.boolSetting("showTestControls", true)
  readonly property bool debug: root.boolSetting("debug", false)

  // The shutdown rule: the battery charge (in percent), the number of
  // minutes into an outage and the remaining runtime (in minutes) that call
  // for the machine to power itself off. Zero switches that half of the rule
  // off, and all of them being zero leaves the whole rule off -- which is
  // how it ships.
  readonly property int shutdownAt: Math.max(0, root.numberSetting("shutdownAt", 0))
  readonly property int shutdownAfterMin: Math.max(0, root.numberSetting("shutdownAfterMin", 0))
  readonly property int shutdownRuntimeMin: Math.max(0, root.numberSetting("shutdownRuntimeMin", 0))
  readonly property int shutdownGraceSec: Math.max(5, root.numberSetting("shutdownGraceSec", 30))

  readonly property bool present: root.status !== null && root.status.battery !== undefined
  readonly property int percent: root.present && typeof root.status.battery === "number"
    ? Math.round(root.status.battery) : -1
  readonly property string state: root.status ? String(root.status.status || "") : ""
  readonly property bool onBattery: root.status ? root.status.onBattery === true : false
  readonly property bool charging: root.status ? root.status.charging === true : false

  readonly property bool critical: root.present && (state === "replaceBattery"
    || state === "overload" || state === "lowBattery"
    || (onBattery && percent >= 0 && percent <= root.criticalBattery))
  readonly property bool warn: root.present && !root.critical && onBattery
    && percent >= 0 && percent <= root.warnBattery

  readonly property color urgentColor: root.bar ? root.bar.urgent : "#ff5555"
  readonly property color normalColor: root.bar ? root.bar.barForeground : "white"

  // Charge glyphs, matching the shell's own power panel so a UPS and a laptop
  // battery never disagree about what "half full" looks like.
  readonly property var chargingGlyphs: ["󰢜", "󰂆", "󰂇", "󰂈", "󰢝", "󰂉", "󰢞", "󰂊", "󰂋", "󰂅"]
  readonly property var chargeGlyphs: ["󰁺", "󰁻", "󰁼", "󰁽", "󰁾", "󰁿", "󰂀", "󰂁", "󰂂", "󰁹"]

  readonly property string glyph: {
    if (root.iconPreset === "Power") return "󰚥"
    if (!root.present && root.iconPreset === "Auto") return "󰚥"
    if (!root.present) return "󰂑"
    var index = Math.max(0, Math.min(9, Math.floor(root.percent / 10)))
    if (root.charging) return root.chargingGlyphs[index]
    return root.chargeGlyphs[index]
  }

  readonly property string barText: {
    if (root.vertical) return root.glyph
    if (root.showPercent && root.present) return root.glyph + " " + root.percent + "%"
    return root.glyph
  }

  // A setting's value comes from the shell as a string unless it was stored
  // as JSON, so "true" has to be read as a boolean too.
  function boolSetting(key, fallback) {
    var value = root.setting(key, fallback)
    if (value === true || value === false) return value
    if (value === "true" || value === 1 || value === "1") return true
    if (value === "false" || value === 0 || value === "0") return false
    return fallback
  }

  // The same for numbers: a stored "0" is a number, not a missing value.
  function numberSetting(key, fallback) {
    var value = root.setting(key, fallback)
    if (typeof value === "string" && value.trim() === "") return fallback
    var number = Number(value)
    return isFinite(number) ? number : fallback
  }

  function statusLabel() {
    return {
      online: "On utility power",
      charging: "Charging",
      fullyCharged: "Fully charged",
      onBattery: "On battery",
      lowBattery: "On battery — low",
      replaceBattery: "Battery needs replacing",
      overload: "Overload",
      off: "Off",
      unknown: "Unknown"
    }[root.state] || (root.state || "Unknown")
  }

  readonly property string tooltip: {
    if (!root.present) return "CyberPower UPS — not detected"
    var name = root.status.model || "CyberPower UPS"
    var parts = [name + " — " + root.statusLabel()]
    if (root.percent >= 0) parts.push(root.percent + "%")
    if (root.status.runtimeMin) parts.push(root.status.runtimeMin + " min left")
    if (root.status.loadPercent !== null && root.status.loadPercent !== undefined)
      parts.push(root.status.loadPercent + "% load")
    if (root.shutdownCountdown >= 0) parts.push("shutting down in " + root.shutdownCountdown + " s")
    else if (root.shutdownAt > 0 || root.shutdownAfterMin > 0 || root.shutdownRuntimeMin > 0)
      parts.push("shutdown " + root.shutdownLabel)
    return parts.join("  ·  ")
  }

  // ---------------------------------------------------------------- polling

  function refresh() {
    if (statusProc.running) return
    if (root.pollStarted && !root.pollFinished) {
      // The process went away without ever exiting: Quickshell could not
      // start it, which means the reader is missing or not executable.
      // Say that, instead of "nothing has reported yet".
      root.status = null
      root.errorText = "cannot run " + root.reader
      root.pollStarted = false
      return
    }
    // The command is set here rather than bound, so a settings change takes
    // effect at the next poll instead of restarting a read in flight.
    statusProc.command = root.commandFor("")
    root.pollStarted = true
    root.pollFinished = false
    statusProc.running = true
  }

  // PopupCard calls owner.close() when the user clicks outside the card.
  function close() {
    root.popupOpen = false
  }

  function runAction(action) {
    if (actionProc.running) return
    root.actionMessage = ""
    actionProc.command = root.commandFor(action)
    actionProc.running = true
  }

  // Build the reader's argv. The optional --ups only matters to the NUT
  // backend; passing it always keeps the call site simple.
  function commandFor(action) {
    var args = [root.reader]
    if (action) args.push(action)
    args.push("--source", root.source)
    if (root.upsName !== "") args.push("--ups", root.upsName)
    if (root.debug) args.push("--debug")
    return args
  }

  // The popup's "Control panel" button opens the full-screen TUI in a
  // terminal. Everything the widget does not expose (alarm on/off, low-battery
  // thresholds, transfer voltages, shutdown delays) lives there.
  function launchTui() {
    if (tuiProc.running) return
    root.popupOpen = false
    var args = ["omarchy-launch-terminal", root.tui, "--source", root.source]
    if (root.upsName !== "") args.push("--ups", root.upsName)
    if (root.debug) args.push("--debug")
    tuiProc.command = args
    tuiProc.running = true
  }

  // ------------------------------------------------------------- shutdown

  // The UPS losing the mains starts the clock the rule reads. The clock
  // lives here because the widget is the one that watches the UPS; the rule
  // itself is the reader's, and is asked for a verdict below.
  function trackOutage() {
    if (!root.onBattery) {
      root.outageStarted = 0
      root.shutdownCancelled = false
      root.clearShutdown()
      return
    }
    if (root.outageStarted <= 0) root.outageStarted = Date.now() / 1000
  }

  // A cheap look before the reader is asked for the authoritative verdict:
  // it only decides whether asking is worth another read of the UPS. Both of
  // its failure modes are safe -- asking too often is answered "no", asking
  // too rarely only means the rule does not fire.
  function checkShutdown() {
    if (root.shutdownAt <= 0 && root.shutdownAfterMin <= 0 && root.shutdownRuntimeMin <= 0) {
      root.clearShutdown()
      return
    }
    if (!root.onBattery || root.shutdownCancelled) {
      root.clearShutdown()
      return
    }
    var elapsed = Date.now() / 1000 - root.outageStarted
    var runtime = root.status ? root.status.runtimeMin : null
    var maybe = (root.shutdownAt > 0 && root.percent >= 0 && root.percent <= root.shutdownAt)
      || (root.shutdownAfterMin > 0 && elapsed >= root.shutdownAfterMin * 60)
      || (root.shutdownRuntimeMin > 0 && typeof runtime === "number" && runtime <= root.shutdownRuntimeMin)
    if (!maybe) {
      root.clearShutdown()
      return
    }
    if (shutdownProc.running) return
    shutdownProc.command = root.shutdownCommand()
    shutdownProc.running = true
  }

  function shutdownCommand() {
    var args = [root.reader, "shutdown", "--source", root.source]
    if (root.upsName !== "") args.push("--ups", root.upsName)
    args.push("--at", String(root.shutdownAt))
    args.push("--after", String(root.shutdownAfterMin))
    args.push("--runtime", String(root.shutdownRuntimeMin))
    if (root.outageStarted > 0) args.push("--outage-start", String(root.outageStarted))
    if (root.debug) args.push("--debug")
    return args
  }

  // The same command again, with --log, so the history says why the machine
  // was powered off. Only the first notice of an outage asks for it.
  function shutdownLogCommand() {
    var args = root.shutdownCommand()
    args.splice(1, 0, "--log")
    return args
  }

  function clearShutdown() {
    root.shutdownTriggered = 0
    root.shutdownCountdown = -1
  }

  function cancelShutdown() {
    root.shutdownCancelled = true
    root.clearShutdown()
    root.shutdownReason = ""
    root.actionMessage = "The shutdown was cancelled"
  }

  // The countdown to the poweroff, on its own timer so the number in the
  // popup is right even between polls.
  function tickShutdown() {
    if (root.shutdownTriggered <= 0) {
      root.shutdownCountdown = -1
      return
    }
    var left = Math.ceil(root.shutdownGraceSec - (Date.now() / 1000 - root.shutdownTriggered))
    root.shutdownCountdown = left
    if (left > 0) return
    root.clearShutdown()
    root.powerOff()
  }

  function powerOff() {
    if (powerProc.running) return
    root.actionMessage = "Shutting down"
    // Omarchy's own shutdown: it closes the windows first and schedules the
    // poweroff in the user manager, so it survives this widget going away.
    powerProc.command = ["omarchy-system-shutdown"]
    powerProc.running = true
  }

  function notify(message) {
    if (notifyProc.running) return
    notifyProc.command = ["notify-send", "CyberPower UPS", message]
    notifyProc.running = true
  }

  Process {
    id: statusProc
    command: []
    stdout: StdioCollector {
      id: statusOut
      waitForEnd: true
    }
    stderr: StdioCollector {
      id: statusErr
      waitForEnd: true
    }
    onExited: function(code) {
      root.pollFinished = true
      var out = String(statusOut.text || "").trim()
      if (code === 0 && out !== "") {
        let parsed = null
        try {
          parsed = JSON.parse(out)
        } catch (error) {
          root.errorText = "Could not parse the reader's output."
        }
        if (parsed !== null) {
          root.status = parsed
          root.errorText = ""
          root.trackOutage()
          root.checkShutdown()
          return
        }
      }
      root.status = null
      var message = String(statusErr.text || "").trim()
      root.errorText = message !== "" ? message : "The UPS reader failed (exit " + code + ")."
    }
  }

  // The reader's verdict on the shutdown rule. It is asked only when the
  // cheap look says the rule might be due, so this is not a read per poll.
  Process {
    id: shutdownProc
    command: []
    stdout: StdioCollector {
      id: shutdownOut
      waitForEnd: true
    }
    stderr: StdioCollector {
      id: shutdownErr
      waitForEnd: true
    }
    onExited: function(code) {
      if (code !== 0) return
      var out = String(shutdownOut.text || "").trim()
      if (out === "") return
      let verdict = null
      try {
        verdict = JSON.parse(out)
      } catch (error) {
        return
      }
      root.shutdownReason = String(verdict.reason || "")
      if (verdict.state === "due") {
        if (root.shutdownTriggered <= 0) {
          root.shutdownTriggered = Date.now() / 1000
          root.notify("Shutting down in " + root.shutdownGraceSec + " s — "
                      + root.shutdownReason)
          // Say it in the history too, so the log explains the poweroff.
          if (!logProc.running) {
            logProc.command = root.shutdownLogCommand()
            logProc.running = true
          }
        }
        return
      }
      root.clearShutdown()
    }
  }

  // The history note. It is a separate process so the verdict is not held up
  // by the write.
  Process {
    id: logProc
    command: []
  }

  // The poweroff itself. systemd asks the seat whether the active session
  // may power the machine off, which it may.
  Process {
    id: powerProc
    command: []
  }

  Process {
    id: notifyProc
    command: []
  }

  Process {
    id: actionProc
    command: []
    stdout: StdioCollector {
      id: actionOut
      waitForEnd: true
    }
    stderr: StdioCollector {
      id: actionErr
      waitForEnd: true
    }
    onExited: function(code) {
      var text = String(actionOut.text || "").trim() || String(actionErr.text || "").trim()
      root.actionMessage = text !== "" ? text : (code === 0 ? "Done" : "Command failed")
      root.refresh()
    }
  }

  // The TUI launcher needs no output of its own; the terminal it spawns shows
  // everything. A refresh on exit picks up whatever was changed there.
  Process {
    id: tuiProc
    command: []
    onExited: root.refresh()
  }

  Timer {
    interval: root.intervalSec * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // The countdown to the poweroff ticks on its own, so the number in the
  // popup does not wait for the next poll.
  Timer {
    interval: 1000
    running: true
    repeat: true
    onTriggered: root.tickShutdown()
  }

  // ------------------------------------------------------------------- bar

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.barText
    fontSize: Style.font.caption
    tooltipText: root.tooltip
    active: root.critical
    foreground: root.warn ? root.urgentColor : root.normalColor
    onPressed: function(mouse) {
      if (mouse === Qt.LeftButton) root.popupOpen = !root.popupOpen
    }
  }

  // ---------------------------------------------------------------- popup

  PopupCard {
    id: popup
    anchorItem: root
    bar: root.bar
    owner: root
    open: root.popupOpen
    contentWidth: popup.fittedContentWidth(Style.space(340))
    contentHeight: popup.fittedContentHeight(body.implicitHeight)

    Column {
      id: body
      anchors.fill: parent
      spacing: Style.space(10)

      // Header ------------------------------------------------------------
      Row {
        id: headerRow
        width: parent.width
        spacing: Style.space(10)

        Text {
          id: headerGlyph
          textFormat: Text.PlainText
          text: root.glyph
          color: root.critical || root.warn ? root.urgentColor : root.normalColor
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.displayLarge
          anchors.verticalCenter: parent.verticalCenter
        }

        Column {
          width: parent.width - headerGlyph.implicitWidth - headerGear.implicitWidth
                 - headerRow.spacing * 2
          spacing: Style.space(2)
          anchors.verticalCenter: parent.verticalCenter

          Text {
            textFormat: Text.PlainText
            text: root.present ? (root.status.model || "CyberPower UPS") : "No UPS detected"
            color: root.normalColor
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.subtitle
            font.bold: true
            elide: Text.ElideRight
            width: parent.width
          }

          Text {
            textFormat: Text.PlainText
            text: root.present ? root.statusLabel() : "Check the connection or the data source"
            color: root.critical || root.warn ? root.urgentColor : Qt.darker(root.normalColor, 1.3)
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideRight
            width: parent.width
          }
        }

        Button {
          id: headerGear
          text: ""
          iconText: "󰒓"
          foreground: root.normalColor
          tooltipText: "Open the full UPS control panel (TUI): alarm, low-battery "
                       + "thresholds, transfer voltages, shutdown delays"
          onClicked: root.launchTui()
          anchors.verticalCenter: parent.verticalCenter
        }
      }

      // Missing-device help ----------------------------------------------
      Column {
        width: parent.width
        spacing: Style.space(4)
        visible: !root.present

        Text {
          textFormat: Text.PlainText
          text: root.errorText !== "" ? root.errorText
                                      : "The UPS reader has not reported anything yet."
          color: Qt.darker(root.normalColor, 1.3)
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
          width: parent.width
        }

        Text {
          textFormat: Text.PlainText
          visible: root.source === "auto" || root.source === "hidraw"
          text: "Direct USB reads need the bundled udev rule so your user may open /dev/hidraw*. "
              + "See udev/ in the plugin folder, or pick pwrstat or NUT as the data source."
          color: Qt.darker(root.normalColor, 1.6)
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
          width: parent.width
        }
      }

      // Readings ----------------------------------------------------------
      PanelSeparator {
        visible: root.present
        foreground: root.normalColor
      }

      Grid {
        visible: root.present
        width: parent.width
        columns: 2
        columnSpacing: Style.space(18)
        rowSpacing: Style.space(4)

        Repeater {
          model: root.readingRows()

          Column {
            required property var modelData
            spacing: Style.space(1)

            Text {
              textFormat: Text.PlainText
              text: modelData.label
              color: Qt.darker(root.normalColor, 1.6)
              font.family: root.bar ? root.bar.fontFamily : Style.font.family
              font.pixelSize: Style.font.caption
            }

            Text {
              textFormat: Text.PlainText
              text: modelData.value
              color: root.normalColor
              font.family: root.bar ? root.bar.fontFamily : Style.font.family
              font.pixelSize: Style.font.bodySmall
            }
          }
        }
      }

      // Controls ----------------------------------------------------------
      PanelSeparator {
        foreground: root.normalColor
      }

      // Wraps to a second line when the popup is too narrow for one row.
      Flow {
        width: parent.width
        spacing: Style.space(6)

        Button {
          text: "Refresh"
          iconText: "󰑐"
          foreground: root.normalColor
          onClicked: root.refresh()
        }

        Button {
          text: "Self-test"
          iconText: "󰊚"
          foreground: root.normalColor
          visible: root.showTestControls
          enabled: root.present && root.source !== "simulate" && !actionProc.running
          opacity: enabled ? 1.0 : 0.4
          tooltipText: "Run the UPS quick battery test (it beeps and runs on battery for a few seconds)"
          onClicked: root.runAction("quick")
        }

        Button {
          text: "Abort"
          iconText: "󰓛"
          foreground: root.normalColor
          visible: root.showTestControls
          enabled: root.present && root.source !== "simulate" && !actionProc.running
          opacity: enabled ? 1.0 : 0.4
          tooltipText: "Abort a running battery test"
          onClicked: root.runAction("abort")
        }

        Button {
          text: "Mute"
          iconText: "󰕾"
          foreground: root.normalColor
          enabled: root.present && root.source !== "simulate" && !actionProc.running
          opacity: enabled ? 1.0 : 0.4
          tooltipText: "Silence the UPS alarm until the next event"
          onClicked: root.runAction("mute")
        }

        Button {
          text: "Cancel shutdown"
          iconText: "󰅖"
          foreground: root.normalColor
          visible: root.shutdownCountdown >= 0
          tooltipText: "Do not power the machine off when the rule is due"
          onClicked: root.cancelShutdown()
        }
      }

      Text {
        textFormat: Text.PlainText
        visible: root.shutdownReason !== ""
        text: root.shutdownReason
        color: Qt.darker(root.normalColor, 1.4)
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
        width: parent.width
      }

      Text {
        textFormat: Text.PlainText
        visible: text !== ""
        text: root.actionMessage
        color: Qt.darker(root.normalColor, 1.4)
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
        width: parent.width
      }

      // Footer ------------------------------------------------------------
      Text {
        textFormat: Text.PlainText
        text: root.footer()
        color: Qt.darker(root.normalColor, 1.7)
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
        width: parent.width
      }
    }
  }

  // ----------------------------------------------------------------- helpers

  function fmt(value, unit, digits) {
    if (value === null || value === undefined || value === "") return ""
    var shown = digits !== undefined ? Number(value).toFixed(digits) : String(value)
    return unit ? shown + unit : shown
  }

  // What the shutdown rule is set to, in one line.
  readonly property string shutdownLabel: {
    if (root.shutdownAt <= 0 && root.shutdownAfterMin <= 0 && root.shutdownRuntimeMin <= 0) return "off"
    var parts = []
    if (root.shutdownAt > 0) parts.push("at " + root.shutdownAt + " % battery")
    if (root.shutdownAfterMin > 0) parts.push("after " + root.shutdownAfterMin + " min")
    if (root.shutdownRuntimeMin > 0) parts.push("at " + root.shutdownRuntimeMin + " min runtime")
    return parts.join(" or ")
  }

  function readingRows() {
    if (!root.present) return []
    var s = root.status
    var rows = []

    if (root.percent >= 0) rows.push({ label: "Battery", value: root.percent + "%" })
    if (s.runtimeMin) rows.push({ label: "Runtime", value: s.runtimeMin + " min" })

    if (root.shutdownAt > 0 || root.shutdownAfterMin > 0 || root.shutdownRuntimeMin > 0) {
      let value = root.shutdownLabel
      if (root.shutdownCountdown >= 0) value = "in " + root.shutdownCountdown + " s"
      rows.push({ label: "Shutdown", value: value })
    }

    var load = root.fmt(s.loadPercent, "%")
    if (s.loadWatts) load += " (" + s.loadWatts + " W)"
    if (load !== "") rows.push({ label: "Load", value: load })

    var input = root.fmt(s.inputVoltage, " V", 1)
    var freq = root.fmt(s.inputFrequency, " Hz", 1)
    if (input !== "" || freq !== "")
      rows.push({ label: "Input", value: [input, freq].filter(function(part) { return part !== "" }).join(" · ") })

    var output = root.fmt(s.outputVoltage, " V", 1)
    if (output !== "") rows.push({ label: "Output", value: output })

    if (s.batteryVoltage) rows.push({ label: "Battery voltage", value: root.fmt(s.batteryVoltage, " V", 1) })
    if (s.temperature !== null && s.temperature !== undefined)
      rows.push({ label: "Temperature", value: root.fmt(s.temperature, " °C", 1) })
    if (s.testResult) rows.push({ label: "Last test", value: String(s.testResult) })
    if (s.lastEvent) rows.push({ label: "Last event", value: String(s.lastEvent) })

    return rows
  }

  function footer() {
    if (!root.present) return "Source: " + root.source
    var source = root.status.source || root.source
    var when = root.status.updated
      ? Qt.formatTime(new Date(root.status.updated * 1000), "hh:mm") : ""
    var parts = ["Source: " + source]
    if (root.status.firmware) parts.push("Firmware " + root.status.firmware)
    if (when) parts.push("updated " + when)
    return parts.join("  ·  ")
  }
}
