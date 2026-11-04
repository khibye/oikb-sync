CHART := charts/oikb-sync

.PHONY: test lint template

test:
	python3 -B -m unittest discover -s tests -v

lint:
	helm lint $(CHART) -f $(CHART)/ci/test-values.yaml
	helm lint $(CHART) -f $(CHART)/ci/full-values.yaml
	helm lint $(CHART) -f $(CHART)/ci/test-values.yaml --set platform=openshift

template:
	helm template oikb-sync $(CHART) -n oikb -f $(CHART)/ci/test-values.yaml
