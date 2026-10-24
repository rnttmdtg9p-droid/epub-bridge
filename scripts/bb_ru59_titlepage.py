from bb_ru59_original import ROOT,SOURCE,translate
import json
u=json.loads((ROOT/"inputs/ru59-titlepage-source.json").read_text());assert u["unit"]==17 and len(u["rows"])==1;SOURCE.append(u);translate(u)
