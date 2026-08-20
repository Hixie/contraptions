BINARY = ghbar
INSTALL_DIR = $(HOME)/.local/bin
AGENT_DIR = $(HOME)/Library/LaunchAgents
AGENT_PREFIX = local.ghbar

$(BINARY): main.swift
	swiftc -O -o $(BINARY) main.swift

run: $(BINARY)
	./$(BINARY)

install: $(BINARY)
	mkdir -p "$(INSTALL_DIR)"
	install -m 755 $(BINARY) "$(INSTALL_DIR)/$(BINARY)"

uninstall:
	for plist in "$(AGENT_DIR)"/$(AGENT_PREFIX).*.plist; do \
	  [ -e "$$plist" ] || continue; \
	  label=`basename "$$plist" .plist`; \
	  launchctl bootout gui/$$(id -u)/$$label 2>/dev/null || true; \
	  rm -f "$$plist"; \
	done
	rm -f "$(INSTALL_DIR)/$(BINARY)"

clean:
	rm -f $(BINARY)

.PHONY: run install uninstall clean
