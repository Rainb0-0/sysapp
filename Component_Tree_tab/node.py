import matplotlib.pyplot as plt

class Node:
    def __init__(self, name):
        self.name = name
        self.children = []
        self.x = 0.0
        self.y = 0.0

    def get_subtree_min_max(self):
        """Returns the minimum and maximum y-coordinates in the subtree."""
        min_y = self.y
        max_y = self.y
        for child in self.children:
            c_min, c_max = child.get_subtree_min_max()
            min_y = min(min_y, c_min)
            max_y = max(max_y, c_max)
        return min_y, max_y

def shift_subtree(node, delta_y):
    """Shifts the y-coordinate of the node and its entire subtree by delta_y."""
    node.y += delta_y
    for child in node.children:
        shift_subtree(child, delta_y)

def layout(node, x, y, min_dy=1.0, delta_x=1.0):
    """
    Recursively assigns coordinates to the node and its subtree.
    - x, y: coordinates of the current node
    - min_dy: minimum y-distance between adjacent subtrees
    - delta_x: fixed x-distance between levels
    """
    node.x = x
    node.y = y
    if not node.children:
        return

    num_children = len(node.children)
    if num_children == 0:
        return

    # Compute base offsets, sorted from highest to lowest (top to bottom)
    if num_children % 2 == 1:
        mid = num_children // 2
        base_offsets = [(i - mid) for i in range(num_children)]
        base_offsets = sorted(base_offsets, reverse=True)  # Top to bottom
    else:
        base_offsets = []
        for i in range(num_children // 2):
            base_offsets.append(i + 0.5)
            base_offsets.append(-(i + 0.5))
        base_offsets = sorted(base_offsets, reverse=True)  # Top to bottom

    # Recursively layout children with temporary y=0
    for child in node.children:
        layout(child, x + delta_x, 0, min_dy, delta_x)

    # Get relative min and max y for each child's subtree
    rel_mins = []
    rel_maxs = []
    for child in node.children:
        c_min, c_max = child.get_subtree_min_max()
        rel_mins.append(c_min)
        rel_maxs.append(c_max)

    # Compute the required scale to ensure min_dy between adjacent subtrees
    scale = min_dy
    for i in range(num_children - 1):
        rel_min_i = rel_mins[i]
        rel_max_ip1 = rel_maxs[i + 1]
        diff_offset = base_offsets[i] - base_offsets[i + 1]
        required = min_dy - (rel_min_i - rel_max_ip1)
        if required > 0:
            required_scale = required / diff_offset
            scale = max(scale, required_scale)

    # Set actual y for each child and shift their subtrees
    for idx, child in enumerate(node.children):
        child_y = node.y + base_offsets[idx] * scale
        shift_subtree(child, child_y - child.y)

def build_tree():
    """Builds the tree by taking user input for the number of levels and nodes."""
    levels = int(input("Enter the total number of levels: "))
    root = Node("root")
    current_level = [root]
    node_counter = {1: 1}  # For naming nodes per level
    for level in range(2, levels + 1):
        if level not in node_counter:
            node_counter[level] = 1
        new_level = []
        for parent in current_level:
            num_children = int(input(f"Enter the number of children for node {parent.name} in level {level}: "))
            for i in range(num_children):
                child_name = f"level{level}-node{node_counter[level]}"
                node_counter[level] += 1
                child = Node(child_name)
                parent.children.append(child)
                new_level.append(child)
        current_level = new_level
    return root

def print_tree(node, prefix=""):
    """Prints the tree with node coordinates in a hierarchical format."""
    print(f"{prefix}{node.name}: ({node.x}, {node.y})")
    for child in node.children:
        print_tree(child, prefix + "  ")

def plot_tree(root):
    """Plots the tree using Matplotlib, drawing nodes and edges."""
    fig, ax = plt.subplots()

    def plot_node(node):
        # Plot the node as a point
        ax.scatter([node.x], [node.y], color='blue', s=100)
        # Add the node name as a label
        ax.text(node.x, node.y + 0.2, node.name, fontsize=10, ha='center')
        # Plot edges to children
        for child in node.children:
            ax.plot([node.x, child.x], [node.y, child.y], color='black')
            plot_node(child)  # Recursively plot children

    plot_node(root)
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_title('Tree Visualization')
    # Ensure equal scaling to preserve distances
    ax.set_aspect('equal')
    # Add some padding around the plot
    all_x = []
    all_y = []
    def collect_coords(node):
        all_x.append(node.x)
        all_y.append(node.y)
        for child in node.children:
            collect_coords(child)
    collect_coords(root)
    x_margin = (max(all_x) - min(all_x)) * 0.1 if all_x else 1
    y_margin = (max(all_y) - min(all_y)) * 0.1 if all_y else 1
    ax.set_xlim(min(all_x) - x_margin, max(all_x) + x_margin)
    ax.set_ylim(min(all_y) - y_margin, max(all_y) + y_margin)
    plt.grid(True)
    plt.show()

# Main execution
if __name__ == "__main__":
    root = build_tree()
    layout(root, 0, 0, min_dy=1.0, delta_x=1.0)
    print("\nFinal node coordinates:")
    print_tree(root)
    plot_tree(root)