WATCHDOG_LABEL = local.fseventsd-watchdog
DAEMON_PLIST = /Library/LaunchDaemons/$(WATCHDOG_LABEL).plist
RECORDER_LABEL = local.fseventsd-log-recorder
AGENT_DIR = $(HOME)/Library/LaunchAgents
AGENT_PLIST = $(AGENT_DIR)/$(RECORDER_LABEL).plist
LOG_DIR = $(HOME)/Library/Logs/fseventsd

$(WATCHDOG_LABEL).plist: $(WATCHDOG_LABEL).plist.template fseventsd-watchdog.sh
	plutil -insert ProgramArguments.2 -string "$$(cat fseventsd-watchdog.sh)" -o $@ $(WATCHDOG_LABEL).plist.template

check:
	python3 -I check-fseventsd.py

test:
	python3 -I test_fseventsd_watchdog.py

install: $(WATCHDOG_LABEL).plist
	sudo install -o root -g wheel -m 644 $(WATCHDOG_LABEL).plist "$(DAEMON_PLIST)"
	sudo launchctl bootout system/$(WATCHDOG_LABEL) 2>/dev/null || true
	sudo launchctl bootstrap system "$(DAEMON_PLIST)"

uninstall:
	sudo launchctl bootout system/$(WATCHDOG_LABEL) 2>/dev/null || true
	sudo rm -f "$(DAEMON_PLIST)"

install-recorder:
	mkdir -p "$(LOG_DIR)" "$(AGENT_DIR)"
	sed "s|@LOGDIR@|$(LOG_DIR)|g" $(RECORDER_LABEL).plist.template > "$(AGENT_PLIST)"
	launchctl bootout gui/$$(id -u)/$(RECORDER_LABEL) 2>/dev/null || true
	launchctl bootstrap gui/$$(id -u) "$(AGENT_PLIST)"

uninstall-recorder:
	launchctl bootout gui/$$(id -u)/$(RECORDER_LABEL) 2>/dev/null || true
	rm -f "$(AGENT_PLIST)"

clean:
	rm -f $(WATCHDOG_LABEL).plist

.PHONY: check test install uninstall install-recorder uninstall-recorder clean
