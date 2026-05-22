from pyrevit import revit, DB, forms, script
import clr

doc = revit.doc


def mm_to_feet(mm):
    return mm / 304.8


def get_selected_grids():
    grids = []

    for el in revit.get_selection():
        if isinstance(el, DB.Grid):
            if isinstance(el.Curve, DB.Line):
                grids.append(el)

    return grids


def get_all_grids():
    return (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.Grid)
        .ToElements()
    )


def get_wall_types():
    wall_types = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.WallType)
        .ToElements()
    )

    result = {}

    for wt in wall_types:
        p = wt.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)

        if p:
            type_name = p.AsString()
        else:
            type_name = "Unnamed"

        result[wt.FamilyName + " - " + type_name] = wt

    return result


def get_line_midpoint(line):
    p1 = line.GetEndPoint(0)
    p2 = line.GetEndPoint(1)

    return DB.XYZ(
        (p1.X + p2.X) / 2.0,
        (p1.Y + p2.Y) / 2.0,
        0
    )


def get_centroid(grids):
    x = 0
    y = 0

    for g in grids:
        m = get_line_midpoint(g.Curve)
        x += m.X
        y += m.Y

    return DB.XYZ(x / len(grids), y / len(grids), 0)


def get_outward_vector(line, centroid):
    start = line.GetEndPoint(0)
    end = line.GetEndPoint(1)

    direction = (end - start).Normalize()

    perp1 = DB.XYZ(-direction.Y, direction.X, 0)
    perp2 = DB.XYZ(direction.Y, -direction.X, 0)

    mid = get_line_midpoint(line)

    d1 = (mid + perp1).DistanceTo(centroid)
    d2 = (mid + perp2).DistanceTo(centroid)

    if d1 > d2:
        return perp1

    return perp2


def offset_line(line, vector, offset):
    move = vector.Multiply(offset)

    s = line.GetEndPoint(0) + move
    e = line.GetEndPoint(1) + move

    return DB.Line.CreateBound(s, e)


def get_intersection_points(line, trim_grids):
    pts = []

    for g in trim_grids:
        if not isinstance(g.Curve, DB.Line):
            continue

        other = g.Curve

        if line == other:
            continue

        try:
            results_ref = clr.Reference[DB.IntersectionResultArray]()
            status = line.Intersect(other, results_ref)

            if status == DB.SetComparisonResult.Overlap:
                results = results_ref.Value

                if results:
                    for i in range(results.Size):
                        pts.append(results.get_Item(i).XYZPoint)

        except:
            pass

    return pts


def trim_line_to_intersections(line, pts):
    if len(pts) < 2:
        return line

    start = line.GetEndPoint(0)
    direction = (line.GetEndPoint(1) - start).Normalize()

    values = []

    for pt in pts:
        vec = pt - start
        t = vec.DotProduct(direction)
        values.append((t, pt))

    values.sort(key=lambda x: x[0])

    return DB.Line.CreateBound(values[0][1], values[-1][1])


def flip_if_needed(wall, vector):
    try:
        orient = wall.Orientation
        dot = orient.DotProduct(vector)

        if dot < 0:
            wall.Flip()
    except:
        pass


def join_walls(walls):
    for i in range(len(walls)):
        for j in range(i + 1, len(walls)):
            try:
                if not DB.JoinGeometryUtils.AreElementsJoined(doc, walls[i], walls[j]):
                    DB.JoinGeometryUtils.JoinGeometry(doc, walls[i], walls[j])
            except:
                pass


class GridWallWindow(forms.WPFWindow):
    def __init__(self):
        xaml = script.get_bundle_file('ui.xaml')
        forms.WPFWindow.__init__(self, xaml)

        self.result = False

        self.btn_create.Click += self.ok_click
        self.btn_cancel.Click += self.cancel_click

    def ok_click(self, sender, args):
        self.result = True
        self.Close()

    def cancel_click(self, sender, args):
        self.Close()


selected_grids = get_selected_grids()

if not selected_grids:
    forms.alert("Select grid lines first.")
    raise SystemExit


wall_types = get_wall_types()

wall_name = forms.SelectFromList.show(
    sorted(wall_types.keys()),
    title="Select Wall Type",
    button_name="Select"
)

if not wall_name:
    raise SystemExit

wall_type = wall_types[wall_name]


window = GridWallWindow()
window.ShowDialog()

if not window.result:
    raise SystemExit


try:
    mode_item = window.combo_mode.SelectedItem
    mode = mode_item.Content.ToString()

    side_a = float(window.txt_side_a.Text)
    side_b = float(window.txt_side_b.Text)

    base_offset = mm_to_feet(float(window.txt_base_offset.Text))
    height = mm_to_feet(float(window.txt_wall_height.Text))

    center_offset_mm = (side_a - side_b) / 2.0
    center_offset = mm_to_feet(center_offset_mm)

except:
    forms.alert("Values must be numeric.")
    raise SystemExit


level = doc.ActiveView.GenLevel

if not level:
    forms.alert("Open floor plan.")
    raise SystemExit


all_grids = get_all_grids()

if "Exterior" in mode:
    trim_grids = selected_grids
    centroid = get_centroid(selected_grids)
else:
    trim_grids = all_grids
    centroid = get_centroid(all_grids)


created = []
skipped = 0


with revit.Transaction("Grid Wall Creator"):

    for grid in selected_grids:
        if not isinstance(grid.Curve, DB.Line):
            skipped += 1
            continue

        base_line = grid.Curve

        pts = get_intersection_points(base_line, trim_grids)

        trimmed = trim_line_to_intersections(base_line, pts)

        direction_vector = get_outward_vector(trimmed, centroid)

        final_line = offset_line(trimmed, direction_vector, center_offset)

        wall = DB.Wall.Create(
            doc,
            final_line,
            wall_type.Id,
            level.Id,
            height,
            base_offset,
            False,
            False
        )

        if "Exterior" in mode:
            flip_if_needed(wall, direction_vector)

        created.append(wall)

    if window.chk_join.IsChecked:
        join_walls(created)


forms.alert(
    "Grid Wall Creator Report\n\n"
    + "Mode: " + mode + "\n"
    + "Created walls: " + str(len(created)) + "\n"
    + "Skipped grids: " + str(skipped) + "\n"
    + "Centerline offset: " + str(center_offset_mm) + " mm"
)