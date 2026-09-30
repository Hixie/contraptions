BINARY = ghbar
INSTALL_DIR = $(HOME)/.local/bin
AGENT_DIR = $(HOME)/Library/LaunchAgents
AGENT_PREFIX = local.ghbar

SOURCES = main.swift flyby.swift
# Not named Info.plist: an Info.plist beside an executable named after its
# directory makes macOS take the directory for an application bundle, and
# then the binary fails signature verification and launchd will not start
# it.
INFO_PLIST = embedded-info.plist

# launchd refuses to start the binary with only the signature the linker
# gives it, so it is signed again. It is built beside the old one and
# renamed over it, because macOS caches the code signature of an
# executable file and refuses to run one rewritten in place.
$(BINARY): $(SOURCES) $(INFO_PLIST)
	swiftc -O -Xlinker -sectcreate -Xlinker __TEXT \
	  -Xlinker __info_plist -Xlinker $(INFO_PLIST) -o $(BINARY).new $(SOURCES)
	codesign --force --sign - --identifier $(AGENT_PREFIX) $(BINARY).new
	mv -f $(BINARY).new $(BINARY)

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
