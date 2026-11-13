BINARY = pingbar
INSTALL_DIR = $(HOME)/.local/bin
AGENT_LABEL = local.pingbar
AGENT_DIR = $(HOME)/Library/LaunchAgents
AGENT_PLIST = $(AGENT_DIR)/$(AGENT_LABEL).plist

$(BINARY): main.swift
	swiftc -O -o $(BINARY) main.swift

run: $(BINARY)
	./$(BINARY)

# Given a job's plist, launchctl bootout returns once launchd has removed the
# job. Given the job's name, it returns while the job's process is still
# exiting, and a launchctl bootstrap straight after fails. uninstall falls back
# to the name when the plist is missing.
install: $(BINARY)
	mkdir -p "$(INSTALL_DIR)" "$(AGENT_DIR)"
	install -m 755 $(BINARY) "$(INSTALL_DIR)/$(BINARY)"
	sed "s|@BINARY@|$(INSTALL_DIR)/$(BINARY)|" $(AGENT_LABEL).plist.template > "$(AGENT_PLIST)"
	launchctl bootout gui/$$(id -u) "$(AGENT_PLIST)" 2>/dev/null || true
	launchctl bootstrap gui/$$(id -u) "$(AGENT_PLIST)"

uninstall:
	launchctl bootout gui/$$(id -u) "$(AGENT_PLIST)" 2>/dev/null \
	  || launchctl bootout gui/$$(id -u)/$(AGENT_LABEL) 2>/dev/null || true
	rm -f "$(AGENT_PLIST)" "$(INSTALL_DIR)/$(BINARY)"

clean:
	rm -f $(BINARY)

.PHONY: run install uninstall clean
