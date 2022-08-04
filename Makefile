.PHONY: default release release-test release-upload

default:
	@echo "This is only for building releases thus far"
	@echo "Select:"
	@echo "   make release"
	@echo "   make release-test"
	@echo "   make release-upload"
	@echo "It will run 'pip install' in your current venv"

release:
	@echo "Is checkout clean?"
	git diff-files --quiet
	git diff-index --quiet --cached HEAD --

	@echo "Install required build software"
	python3 -m pip install --editable .[build]

	@echo "Test README"
	python3 setup.py check -r -s

	@echo "Update NEWS"
	python3 -m towncrier build --yes --version `python3 misc/build_helpers/update-version.py --no-tag`
	git add -u
	git commit -m "update NEWS for release"

	@echo "Bump version and create tag"
	python3 misc/build_helpers/update-version.py

	@echo "Build and sign wheel"
	python3 setup.py bdist_wheel
	gpg --pinentry=loopback -u meejah@meejah.ca --armor --detach-sign dist/magic_folder-`git describe --abbrev=0`-py3-none-any.whl
	ls dist/*`git describe --abbrev=0`*

	@echo "Build and sign source-dist"
	python3 setup.py sdist
	gpg --pinentry=loopback -u meejah@meejah.ca --armor --detach-sign dist/magic-folder-`git describe --abbrev=0`.tar.gz
	ls dist/*`git describe --abbrev=0`*

release-test:
	gpg --verify dist/magic-folder-`git describe --abbrev=0`.tar.gz.asc
	gpg --verify dist/magic_folder-`git describe --abbrev=0`-py3-none-any.whl.asc
	virtualenv testmf_venv
	testmf_venv/bin/pip install dist/magic_folder-`git describe --abbrev=0`-py3-none-any.whl
	testmf_venv/bin/magic-folder --version
	testmf_venv/bin/magic-folder-api --version
	testmf_venv/bin/pip uninstall -y magic_folder
	testmf_venv/bin/pip install dist/magic-folder-`git describe --abbrev=0`.tar.gz
	testmf_venv/bin/magic-folder --version
	testmf_venv/bin/magic-folder-api --version
	rm -rf testmf_venv

release-upload:
	twine upload dist/magic_folder-`git describe --abbrev=0`-py3-none-any.whl dist/magic_folder-`git describe --abbrev=0`-py3-none-any.whl.asc dist/magic-folder-`git describe --abbrev=0`.tar.gz dist/magic-folder-`git describe --abbrev=0`.tar.gz.asc
