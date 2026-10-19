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

  readonly property string reader: decodeURIComponent(
    String(Qt.resolvedUrl("bin/cyberpower-ups")).replace("file://", ""))
  readonly property string tui: decodeURIComponent(
    String(Qt.resolvedUrl("bin/cyberpower-ups-tui")).replace("file://", ""))

  // The reader speaks in lowercase tokens; the settings UI shows prose.
  readonly property string source: {
    var choice = root.setting("source", "Auto")
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
        try {
          root.status = JSON.parse(out)
          root.errorText = ""
          return
        } catch (error) {
          root.errorText = "Could not parse the reader's output."
        }
      }
      root.status = null
      var message = String(statusErr.text || "").trim()
      root.errorText = message !== "" ? message : "The UPS reader failed (exit " + code + ")."
    }
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

  function readingRows() {
    if (!root.present) return []
    var s = root.status
    var rows = []

    if (root.percent >= 0) rows.push({ label: "Battery", value: root.percent + "%" })
    if (s.runtimeMin) rows.push({ label: "Runtime", value: s.runtimeMin + " min" })

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
