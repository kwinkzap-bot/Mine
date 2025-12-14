from typing import Tuple, Union

class CPRService:
    @staticmethod
    def calculate_cpr(high: Union[float, int], low: Union[float, int], close: Union[float, int]) -> Tuple[float, float, float]:
        """Calculates Central Pivot Range (PP, BC, TC)."""
        pp = (high + low + close) / 3
        bc = (high + low) / 2
        tc = (2 * pp) - bc
        
        # Ensure BC/TC are returned as lower/upper for consistency, although the formulas define them
        lower = min(bc, tc)
        upper = max(bc, tc)
        
        return pp, lower, upper # pp, BC, TC (where BC/TC are lower/upper bounds)