#import "Views.h"
#import "Library.h"
#import "Server.h"

@implementation TracksView {
    Library *_library;
    Server *_server;
    NSArray *_tracks, *_queue;
    NSMutableDictionary *_failures;
    NSURL *_spotifyURL;
    NSURLSessionDownloadTask *_task;
    NSString *_status;
    NSUInteger _cursor, _saved, _existing, _missing, _failed, _batch;
}

- (instancetype)initWithPlaylist:(NSDictionary *)playlist server:(Server *)server library:(Library *)library {
    if ((self = [super initWithStyle:UITableViewStyleGrouped])) {
        _library = library; _server = server; _tracks = playlist[@"tracks"];
        _failures = [NSMutableDictionary dictionary];
        self.title = Text(playlist[@"name"], @"Playlist");
        NSURLComponents *source = [NSURLComponents componentsWithString:Text(playlist[@"spotify_url"], @"")];
        if ([source.scheme.lowercaseString isEqual:@"https"] && [source.host.lowercaseString isEqual:@"open.spotify.com"] &&
            !source.user && !source.password && !source.port && [source.percentEncodedPath rangeOfString:
                @"^/playlist/[A-Za-z0-9]{22}$" options:NSRegularExpressionSearch].location != NSNotFound) {
            _spotifyURL = [NSURL URLWithString:[@"https://open.spotify.com" stringByAppendingString:source.percentEncodedPath]];
        }
    }
    return self;
}
- (void)viewDidLoad {
    [super viewDidLoad];
    if (!_spotifyURL) return;
    UIButton *source = [UIButton buttonWithType:UIButtonTypeSystem];
    source.frame = CGRectMake(0, 0, self.tableView.bounds.size.width, 48);
    [source setTitle:@"Open in Spotify" forState:UIControlStateNormal];
    [source addTarget:self action:@selector(openSpotify) forControlEvents:UIControlEventTouchUpInside];
    self.tableView.tableHeaderView = source;
}
- (void)openSpotify {
    if (![[UIApplication sharedApplication] openURL:_spotifyURL]) {
        _status = @"Could not open the Spotify playlist link.";
        [self update];
    }
}
- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    [self update];
}
- (void)viewDidDisappear:(BOOL)animated {
    [super viewDidDisappear:animated];
    if (![self.navigationController.viewControllers containsObject:self]) [self cancel];
}
- (void)update {
    self.navigationItem.rightBarButtonItem = _queue ? Button(@"Cancel", self, @selector(cancel)) :
        Button(@"Save matched", self, @selector(saveAll));
    [self.tableView reloadData];
}
- (void)cancel {
    if (!_queue) return;
    _batch++; [_task cancel]; _task = nil; _queue = nil;
    _status = @"Stopped. Finished downloads are in On My iPad.";
    [self update];
}
- (void)advance {
    while (_cursor < _queue.count) {
        NSDictionary *track = _queue[_cursor++];
        NSString *tid = MatchedID(track);
        if (!tid) { _missing++; continue; }
        if ([_library containsTrack:track origin:_server.origin]) { _existing++; continue; }
        _status = [NSString stringWithFormat:@"Saving %lu of %lu · %@", (unsigned long)_cursor,
                   (unsigned long)_queue.count, Text(track[@"title"], @"Untitled track")];
        [self update];
        NSUInteger batch = _batch;
        _task = [_server download:track into:_library completion:^(NSString *error) {
            if (batch != self->_batch) return;
            self->_task = nil;
            if (error) { self->_failures[tid] = error; self->_failed++; }
            else { [self->_failures removeObjectForKey:tid]; self->_saved++; }
            dispatch_async(dispatch_get_main_queue(), ^{ if (batch == self->_batch) [self advance]; });
        }];
        return;
    }
    _queue = nil;
    _status = [NSString stringWithFormat:@"%lu saved · %lu already on iPad · %lu missing · %lu failed",
               (unsigned long)_saved, (unsigned long)_existing, (unsigned long)_missing, (unsigned long)_failed];
    [self update];
}
- (void)start:(NSArray *)tracks {
    if (_queue) return;
    _queue = tracks; _cursor = _saved = _existing = _missing = _failed = 0;
    _batch++;
    [self advance];
}
- (void)saveAll { [self start:_tracks]; }

- (NSInteger)tableView:(UITableView *)table numberOfRowsInSection:(NSInteger)section { return _tracks.count; }
- (NSString *)tableView:(UITableView *)table titleForHeaderInSection:(NSInteger)section {
    return _status ?: @"Tap a matched track to save it, or choose Save matched.";
}
- (NSString *)tableView:(UITableView *)table titleForFooterInSection:(NSInteger)section {
    return @"Missing tracks need a matching upload on the server. Tap a failed track to retry.";
}
- (UITableViewCell *)tableView:(UITableView *)table cellForRowAtIndexPath:(NSIndexPath *)path {
    NSDictionary *t = _tracks[path.row];
    NSString *tid = MatchedID(t);
    BOOL saved = tid && [_library containsTrack:t origin:_server.origin];
    NSString *error = tid ? _failures[tid] : nil;
    NSString *state = saved ? @"On My iPad" : (!tid ? @"Missing on server" :
        (error ? [@"Failed: " stringByAppendingString:error] : @"Tap to save"));
    UITableViewCell *cell = Row(table, @"track");
    cell.textLabel.text = Text(t[@"title"], @"Untitled track");
    cell.detailTextLabel.text = [NSString stringWithFormat:@"%@ · %@", Text(t[@"artist"], @"Unknown artist"), state];
    cell.accessoryType = saved ? UITableViewCellAccessoryCheckmark : UITableViewCellAccessoryNone;
    return cell;
}
- (void)tableView:(UITableView *)table didSelectRowAtIndexPath:(NSIndexPath *)path {
    [table deselectRowAtIndexPath:path animated:YES];
    if (!_queue) [self start:@[_tracks[path.row]]];
}
@end
