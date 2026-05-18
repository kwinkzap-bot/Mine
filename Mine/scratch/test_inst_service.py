
import sys
import os
import json

# Add src to path
sys.path.append('/Users/kavinkumar/Mine/Mine/src')

from trading_app.service.institutional_service import InstitutionalService

if __name__ == "__main__":
    data = InstitutionalService.get_latest_data()
    print(json.dumps(data, indent=2))
