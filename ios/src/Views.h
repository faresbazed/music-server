#import <UIKit/UIKit.h>
@class Library, Server;

@interface DownloadsView : UITableViewController
- (instancetype)initWithLibrary:(Library *)library;
@end

@interface PlaylistsView : UITableViewController
- (instancetype)initWithLibrary:(Library *)library;
@end

@interface TracksView : UITableViewController
- (instancetype)initWithPlaylist:(NSDictionary *)playlist server:(Server *)server library:(Library *)library;
@end

static inline UITableViewCell *Row(UITableView *table, NSString *identifier) {
    return [table dequeueReusableCellWithIdentifier:identifier] ?:
        [[UITableViewCell alloc] initWithStyle:UITableViewCellStyleSubtitle reuseIdentifier:identifier];
}
static inline UIBarButtonItem *Button(NSString *title, id target, SEL action) {
    return [[UIBarButtonItem alloc] initWithTitle:title style:UIBarButtonItemStylePlain target:target action:action];
}
