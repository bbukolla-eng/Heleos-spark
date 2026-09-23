"""Temporary local browser smoke fixture; synthetic inputs, no recognition claim."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests/drawing-workspace'))
from test_equipment_count_workflow import EquipmentCountWorkflowTests, fixtures
case=EquipmentCountWorkflowTests('test_correct_reopen_replay_and_export_preserve_prior_counts')
case.setUp()
case.workspace.metadata['project']['name']='Synthetic equipment acceptance'
case.save(case.draft())
case.command('navigate', {'stage':'equipment'})
server=fixtures.drawing.create_server(case.workspace)
server.token='equipment-acceptance'
print(server.origin+'/'+server.token+'/',flush=True)
try: server.serve_forever()
finally:
 server.server_close()
 case.doCleanups()
