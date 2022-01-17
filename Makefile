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
	pip install --editable .[build]

	@echo "Update NEWS"
	python -m towncrier --yes --version `python misc/build_helpers/update-version.py --no-tag`
	git add -u
	git commit -m "update NEWS for release"

	@echo "Bump version and create tag"
	python misc/build_helpers/update-version.py

	@echo "Build and sign wheel"
	python setup.py bdist_wheel
	gpg --pinentry=loopback -u meejah@meejah.ca --armor --detach-sign dist/magic_folder-`git describe --abbrev=0`-py3-none-any.whl
	ls dist/*`git describe --abbrev=0`*

release-test:
	virtualenv testmf_venv
	testmf_venv/bin/pip install dist/magic_folder-`git describe --abbrev=0`-py3-none-any.whl
	testmf_venv/bin/magic-folder --version
	testmf_venv/bin/magic-folder-api --version
	rm -rf testmf_venv

release-upload:
	twine upload -r testpypi dist/magic_folder-`git describe --abbrev=0`-py3-none-any.whl dist/magic_folder-`git describe --abbrev=0`-py3-none-any.whl.asc
