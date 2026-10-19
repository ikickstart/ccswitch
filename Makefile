PREFIX ?= $(HOME)/.local
BIN    := $(PREFIX)/bin

.PHONY: test install uninstall

test:
	python3 -m unittest discover -s tests -v

install:
	mkdir -p $(BIN)
	ln -sfn $(CURDIR)/ccswitch $(BIN)/ccswitch
	@echo "installed $(BIN)/ccswitch (make sure $(BIN) is on your PATH)"

uninstall:
	rm -f $(BIN)/ccswitch
